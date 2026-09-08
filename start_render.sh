#!/usr/bin/env bash
set -Eeuo pipefail

# Render starts one process per web service. The API starts the scheduler as a
# managed child process, so all scheduled work shares its persistent SQLite
# database and generated podcast files.
children=()

stop_children() {
  local status=$?
  trap - EXIT INT TERM
  for pid in "${children[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  exit "$status"
}
trap stop_children EXIT INT TERM

if [[ -n "${REDIS_URL:-}" || ( -n "${REDIS_HOST:-}" && "${REDIS_HOST:-}" != "localhost" ) ]]; then
  python -m celery_worker &
  children+=("$!")
else
  echo "REDIS_URL/REDIS_HOST is not configured; Celery worker is not started."
fi

python main.py &
children+=("$!")

# If any essential process fails, stop the rest and let Render restart it.
wait -n "${children[@]}"
