#!/usr/bin/env bash
# Visual regression of a built image (hosh-management AGENTS.md section 3, use cases in TESTING.md).
#   visual/run.sh [IMAGE] [--update]      (default livres:local)
# Starts the image hardened as on the box (uid 10001, read-only rootfs, no caps, no-new-privileges) on an
# empty volume with the scheduler off, seeds fixed data (visual/seed.py: live search off, so nothing leaves
# the container), then visual/shoot.mjs shoots every use case at 1400 / 1024 / 390 and compares with
# visual/baseline/. The admin password is the harness's own (visual/cases.mjs), set only in this container.
# Needs: docker, node >= 22, a Chrome ($CHROME, default google-chrome). No package is installed.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${1:-livres:local}"
shift || true
PORT="${VISUAL_PORT:-18350}"
NAME="livres-visual-$$"
VOL="livres-visual-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$VOL" >/dev/null 2>&1 || true' EXIT

PASSWORD="$(sed -n 's/^export const ADMIN_PASSWORD = "\(.*\)";$/\1/p' visual/cases.mjs)"
HASH="$(docker run --rm --network none -e PW="$PASSWORD" "$IMAGE" \
  python -c 'import os; from livres.web.auth import hash_password; print(hash_password(os.environ["PW"]))')"

docker run -d --name "$NAME" --read-only --tmpfs /tmp --cap-drop ALL --user 10001:10001 \
  --security-opt no-new-privileges:true --pids-limit 128 --memory 512m \
  -e LIVRES_SCHEDULER=off -e APP_BASE_URL="http://localhost:$PORT" -e LIVRES_ADMIN_PASSWORD_HASH="$HASH" \
  -v "$VOL:/data" -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null
for _ in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || { docker logs "$NAME"; exit 1; }
docker exec -i "$NAME" python - < visual/seed.py

status=0
# localhost: the admin cookie is Secure, and Chrome keeps Secure cookies on http://localhost only
node visual/shoot.mjs "http://localhost:$PORT" "$@" || status=$?
if [ "$status" != 0 ]; then docker logs "$NAME" 2>&1 | tail -n 20; fi
exit "$status"
