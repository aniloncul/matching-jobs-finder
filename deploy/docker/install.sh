#!/usr/bin/env bash
# One-command JobScout install (Docker required).
#
#   curl -fsSL https://raw.githubusercontent.com/aniloncul/matching-jobs-finder/analysis/repo-benchmark/deploy/docker/install.sh | bash
#
# Environment overrides:
#   PORT=8080            host port (bound to 127.0.0.1)
#   NAME=jobscout        container name
#   IMAGE=ghcr.io/...    image to run
#   JOBSCOUT_PASSWORD=…  enable HTTP basic auth (needed if you expose it)
#   OPEN_DATA=1          also write data to ./jobscout-data instead of a volume
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/aniloncul/matching-jobs-finder:latest}"
NAME="${NAME:-jobscout}"
PORT="${PORT:-8080}"
VOLUME="${VOLUME:-jobscout_data}"

if ! command -v docker >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Docker is required. Install Docker Desktop (macOS/Windows) or Docker Engine:
  https://docs.docker.com/get-docker/
EOF
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker and re-run this script." >&2
  exit 1
fi

echo "== pulling $IMAGE"
docker pull "$IMAGE" >/dev/null

echo "== (re)starting container '$NAME' on 127.0.0.1:$PORT"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker volume create "$VOLUME" >/dev/null

run_args=(
  -d --name "$NAME" --restart unless-stopped
  -p "127.0.0.1:${PORT}:8000"
  -v "${VOLUME}:/app/data"
  --memory=2g
  -e JOBSCOUT_HOST=0.0.0.0
  -e JOBSCOUT_PORT=8000
)
if [ -n "${JOBSCOUT_PASSWORD:-}" ]; then
  run_args+=(-e "JOBSCOUT_PASSWORD=${JOBSCOUT_PASSWORD}")
fi

docker run "${run_args[@]}" "$IMAGE" >/dev/null

echo "== waiting for the app"
for _ in $(seq 1 45); do
  if curl -fsS -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS -m 5 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "The app did not answer. Inspect logs with: docker logs -f $NAME" >&2
  exit 1
fi

URL="http://127.0.0.1:${PORT}"
echo
echo "JobScout is running:  $URL"
if [ -n "${JOBSCOUT_PASSWORD:-}" ]; then
  echo "Sign in as 'jobscout' with the password you set."
else
  echo "No password: the port is bound to loopback, so only this machine can open it."
fi
echo
echo "Next: create a Google Sheet, then paste it plus your model API key in SETUP."
echo "Logs:   docker logs -f $NAME"
echo "Stop:   docker rm -f $NAME   (data stays in the '$VOLUME' volume)"

if [ "${NO_OPEN:-0}" != "1" ]; then
  if command -v open >/dev/null 2>&1; then open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
