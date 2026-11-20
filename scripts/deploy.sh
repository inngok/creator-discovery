#!/usr/bin/env bash
set -euo pipefail

deploy_path="${1:?Missing deployment directory}"
commit="${2:?Missing deployment commit SHA}"
branch="${3:?Missing deployment branch}"
api_service="${4:?Missing API systemd service name}"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid commit SHA' >&2; exit 1; }
[[ "$api_service" =~ ^[a-zA-Z0-9_.@-]+\.service$ ]] || { echo 'Invalid API service name' >&2; exit 1; }
cd "$deploy_path"
exec 9>.git/deploy.lock
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
test -f .env
test -x venv/bin/python
services=("$api_service" crawler-worker.service crawler-beat.service)
for service in "${services[@]}"; do
  [[ "$(systemctl show --property=LoadState --value "$service")" == loaded ]] || {
    echo "Missing systemd service: $service" >&2; exit 1;
  }
done
git diff --quiet
git diff --cached --quiet
git fetch origin "$branch"
if [[ "$(git rev-parse FETCH_HEAD)" != "$commit" ]]; then
  echo 'Branch has moved; run the workflow for its latest commit.' >&2
  exit 1
fi
# Fail before changing files if the VPS has divergent or newer commits.
git merge-base --is-ancestor HEAD "$commit"
previous_commit="$(git rev-parse HEAD)"
git merge --ff-only "$commit"
echo "Deploying $commit (previous commit: $previous_commit)"
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip check
venv/bin/python -m compileall -q app main.py
systemctl restart "${services[@]}"

all_services_active() {
  local service
  for service in "${services[@]}"; do
    systemctl is-active --quiet "$service" || return 1
  done
}

for attempt in $(seq 1 30); do
  if all_services_active && \
    venv/bin/python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=5)' \
    >/dev/null 2>&1; then
    echo "Deployment successful: $commit"
    exit 0
  fi
  sleep 5
done
systemctl --no-pager --full status "${services[@]}" || true
echo "Deployment check failed. Previous commit: $previous_commit; inspect services before manual rollback." >&2
exit 1
