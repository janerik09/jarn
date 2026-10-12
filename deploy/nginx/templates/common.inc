# Shared by every mode: the dashboard, the API (same origin) and the WebSocket used by job monitoring.
root /usr/share/nginx/html;
index index.html;
client_max_body_size 1m;
add_header X-Content-Type-Options nosniff always;

location /api/ {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $backend http://backend:8000;
    proxy_pass $backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
}

# Health only. /metrics is deliberately not forwarded to the public port (Prometheus scrapes the backend directly)
location = /health {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $backend http://backend:8000;
    proxy_pass $backend;
}

# Not published on the web port. Without this the catch-all below would answer with the dashboard page.
location = /metrics {
    return 404;
}

location /assets/ {
    try_files $uri =404;
    add_header Cache-Control "public, max-age=31536000, immutable";
}

location / {
    try_files $uri $uri/ /index.html;
}
