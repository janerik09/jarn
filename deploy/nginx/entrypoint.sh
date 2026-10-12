#!/bin/sh
# Renders the nginx config from the TLS settings the backend writes to /tls, checks it, and reloads.
# A config that fails the check is not applied; the previous one keeps running and the error is reported.
TLS=/tls
TEMPLATES=/etc/nginx/templates
ACTIVE=/etc/nginx/conf.d/default.conf
PREV=/tmp/previous-default.conf
TEST_LOG=/tmp/nginx-test.log
NGINX_PID=""

mkdir -p "$TLS"

running() {
    [ -n "$NGINX_PID" ] && kill -0 "$NGINX_PID" 2>/dev/null
}

start_or_reload() {
    if running; then
        kill -HUP "$NGINX_PID"
    else
        nginx -g 'daemon off;' &
        NGINX_PID=$!
    fi
}

write_status() {
    # $1 mode in effect, $2 ok (true/false), $3 message
    msg=$(printf '%s' "$3" | tr '\n\r"\\' '    ')
    printf '{"mode":"%s","ok":%s,"error":"%s","applied_at":"%s"}\n' \
        "$1" "$2" "$msg" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$TLS/applied.json.tmp"
    mv "$TLS/applied.json.tmp" "$TLS/applied.json"
}

choose() {
    TLS_MODE=http
    REDIRECT_HTTPS=0
    if [ -f "$TLS/mode.env" ]; then . "$TLS/mode.env"; fi
    EFFECTIVE=http
    NOTE=""
    case "$TLS_MODE" in
        self_signed|custom)
            if [ -s "$TLS/server.crt" ] && [ -s "$TLS/server.key" ]; then
                EFFECTIVE=$TLS_MODE
                if [ "$REDIRECT_HTTPS" = 1 ]; then
                    CHOSEN=$TEMPLATES/https_redirect.conf
                else
                    CHOSEN=$TEMPLATES/https.conf
                fi
            else
                NOTE="certificate files are missing, serving HTTP"
                CHOSEN=$TEMPLATES/http.conf
            fi
            ;;
        *)
            CHOSEN=$TEMPLATES/http.conf
            ;;
    esac
}

apply() {
    choose
    [ -f "$ACTIVE" ] && cp "$ACTIVE" "$PREV"
    cp "$CHOSEN" "$ACTIVE"
    if nginx -t > "$TEST_LOG" 2>&1; then
        start_or_reload
        sleep 1
        if running; then
            write_status "$EFFECTIVE" true "$NOTE"
            echo "tls: serving $EFFECTIVE redirect=$REDIRECT_HTTPS $NOTE"
            return 0
        fi
    fi
    err=$(cat "$TEST_LOG")
    echo "tls: new config rejected, keeping the previous one"
    if running && [ -f "$PREV" ]; then
        cp "$PREV" "$ACTIVE"
        nginx -t > "$TEST_LOG" 2>&1 && kill -HUP "$NGINX_PID"
    elif ! running; then
        cp "$TEMPLATES/http.conf" "$ACTIVE"
        nginx -t > "$TEST_LOG" 2>&1 && start_or_reload
    fi
    write_status "$TLS_MODE" false "$err"
    return 1
}

fingerprint() {
    cat "$TLS/mode.env" "$TLS/server.crt" "$TLS/server.key" 2>/dev/null | md5sum
}

apply
last=$(fingerprint)
while true; do
    sleep 3
    if ! running; then
        echo "tls: nginx is not running, exiting so the container restarts"
        exit 1
    fi
    now=$(fingerprint)
    if [ "$now" != "$last" ]; then
        apply
        last=$now
    fi
done
