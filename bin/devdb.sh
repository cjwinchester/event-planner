#!/usr/bin/env bash
# Local dev Postgres (PostGIS) on 127.0.0.1:5433, data in .pgdata/ (gitignored).
# Uses Postgres.app's binaries but its own cluster, so it works from any shell.
#
#   bash bin/devdb.sh          start (creates the cluster + database the first time)
#   bash bin/devdb.sh stop
#   bash bin/devdb.sh status
#
# .env: DATABASE_URL=postgis://postgres@127.0.0.1:5433/event_planner
set -euo pipefail
cd "$(dirname "$0")/.."
BIN="${PG_BIN:-/Applications/Postgres.app/Contents/Versions/latest/bin}"
DATA=.pgdata/data
PORT="${PG_PORT:-5433}"

case "${1:-start}" in
  start)
    if [ ! -f "$DATA/PG_VERSION" ]; then
      mkdir -p .pgdata
      "$BIN/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8 >/dev/null
      fresh=1
    fi
    if "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
      echo "Already running on port $PORT."
    else
      "$BIN/pg_ctl" -D "$DATA" -l .pgdata/postgres.log -o "-p $PORT -k '' -h 127.0.0.1" -w start
    fi
    if [ -n "${fresh:-}" ]; then
      "$BIN/createdb" -h 127.0.0.1 -p "$PORT" -U postgres event_planner
      echo "New empty database: run 'uv run python manage.py migrate'."
    fi
    ;;
  stop) "$BIN/pg_ctl" -D "$DATA" stop ;;
  status) "$BIN/pg_ctl" -D "$DATA" status ;;
  *) echo "usage: $0 [start|stop|status]" >&2; exit 2 ;;
esac
