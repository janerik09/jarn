#!/bin/sh
# Database backups for JÁRN. Each run dumps the database, checks the dump can be read, and every RESTORE_CHECK_DAYS
# restores the newest dump into a scratch database to prove it works. Use --once to run a single cycle.
set -u
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
EVERY=${BACKUP_EVERY_SECONDS:-86400}
RESTORE_CHECK_DAYS=${RESTORE_CHECK_DAYS:-7}
MARKER=/backups/.last_restore_check
CHECK_DB=patching_restore_check
export PGHOST=postgres PGUSER=patching_user

restore_check() {
    dump="$1"
    psql -d postgres -qc "DROP DATABASE IF EXISTS $CHECK_DB" && psql -d postgres -qc "CREATE DATABASE $CHECK_DB" || return 1
    if pg_restore --no-owner -d "$CHECK_DB" "$dump" 2>/tmp/restore.err; then
        tables=$(psql -d "$CHECK_DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        psql -d postgres -qc "DROP DATABASE IF EXISTS $CHECK_DB"
        if [ "${tables:-0}" -ge 16 ]; then
            echo "restore check passed: $tables tables restored from $(basename "$dump")"
            return 0
        fi
        echo "RESTORE CHECK FAILED: only ${tables:-0} tables restored"
    else
        echo "RESTORE CHECK FAILED: $(cat /tmp/restore.err)"
        psql -d postgres -qc "DROP DATABASE IF EXISTS $CHECK_DB" >/dev/null 2>&1
    fi
    return 1
}

cycle() {
    mkdir -p /backups
    stamp=$(date -u +%Y%m%d-%H%M%S)
    file=/backups/patching_db-$stamp.dump
    if pg_dump -d patching_db -Fc -f "$file"; then
        if pg_restore --list "$file" >/dev/null 2>&1; then
            echo "backup ok: $(basename "$file")"
        else
            echo "BACKUP VERIFY FAILED: $(basename "$file")"
            rm -f "$file"
            return 1
        fi
    else
        echo "BACKUP FAILED"
        return 1
    fi
    find /backups -name 'patching_db-*.dump' -mtime +"$KEEP_DAYS" -delete
    if [ ! -f "$MARKER" ] || [ -n "$(find "$MARKER" -mtime +"$RESTORE_CHECK_DAYS" 2>/dev/null)" ]; then
        if restore_check "$file"; then touch "$MARKER"; fi
    fi
    return 0
}

if [ "${1:-}" = "--once" ]; then
    cycle
    exit $?
fi
while true; do
    cycle
    sleep "$EVERY"
done
