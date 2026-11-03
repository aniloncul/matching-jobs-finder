#!/usr/bin/env bash
# JobScout on an Oracle Cloud Always Free ARM VM (Ubuntu 22.04/24.04).
#
#   bash setup.sh "<JOBSCOUT_PASSWORD>"
#
# Installs Docker, builds the image from this repo, starts the app on port
# 8000 with a persistent volume and HTTP basic auth, and opens the port in
# the VM firewall (the VCN security list is a separate, console-side rule).
set -euo pipefail

PASSWORD="${1:-}"
if [ -z "$PASSWORD" ]; then
  echo "usage: bash setup.sh \"<JOBSCOUT_PASSWORD>\"" >&2
  exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/aniloncul/matching-jobs-finder.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-$HOME/jobscout}"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

need_pkg=()
command -v git >/dev/null 2>&1 || need_pkg+=(git)
command -v docker >/dev/null 2>&1 || need_pkg+=(docker.io)
if [ "${#need_pkg[@]}" -gt 0 ]; then
  $SUDO apt-get update -y
  $SUDO apt-get install -y "${need_pkg[@]}"
fi
$SUDO systemctl enable --now docker

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" checkout -q -B "$BRANCH" FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
echo "== building image (first run: 10-20 min on a 4-core A1) =="
$SUDO docker build -t jobscout:local .

$SUDO docker volume create jobscout_data >/dev/null
$SUDO docker rm -f jobscout >/dev/null 2>&1 || true
$SUDO docker run -d --name jobscout --restart unless-stopped \
  -p 8000:8000 \
  -v jobscout_data:/app/data \
  -e JOBSCOUT_PASSWORD="$PASSWORD" \
  jobscout:local

# Oracle's Ubuntu images ship with an iptables REJECT rule; open the port.
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
  $SUDO ufw allow 8000/tcp
fi
if command -v iptables >/dev/null 2>&1; then
  $SUDO iptables -C INPUT -p tcp --dport 8000 -j ACCEPT 2>/dev/null \
    || $SUDO iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
  if command -v netfilter-persistent >/dev/null 2>&1; then
    $SUDO netfilter-persistent save
  fi
fi

echo "== waiting for the app =="
for _ in $(seq 1 30); do
  if curl -fsS -m 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

IP="$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || true)"
echo
echo "JobScout is up:  http://${IP:-<instance-public-ip>}:8000"
echo "Sign in with user 'jobscout' and the password you just set."
echo "Logs: sudo docker logs -f jobscout"
