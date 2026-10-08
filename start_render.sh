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

# Running the worker alongside the API duplicates the application's sizeable
# dependency graph. On the 512 MB Render web instance this can cause the OS to
# kill a process mid-generation, which presents to the browser as a CORS/network
# error even though the origin policy is correct. Production uses the API's
# bounded local executor unless Celery has been explicitly enabled.
if [[ "${PODCAST_AGENT_USE_CELERY:-false}" =~ ^(1|true|yes|on)$ ]] && \
   [[ -n "${REDIS_URL:-}" || ( -n "${REDIS_HOST:-}" && "${REDIS_HOST:-}" != "localhost" ) ]]; then
  python -m celery_worker &
  children+=("$!")
else
  echo "Celery worker is disabled or Redis is not configured; using the API's local task executor."
fi

python main.py &
children+=("$!")

# If any essential process fails, stop the rest and let Render restart it.
wait -n "${children[@]}"
