#!/bin/bash
# Run this on the server after git push to pull, install deps, and restart the app.
# Usage: cd /root/social-video-downloader && ./scripts/update.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "Pulling latest..."
git pull
cd backend
if [ ! -f .venv/bin/python3 ]; then
  echo "Creating venv..."
  python3 -m venv .venv
  # Some systems create venv without pip; bootstrap it (ensurepip or get-pip.py)
  if ! .venv/bin/python3 -m pip --version &>/dev/null; then
    .venv/bin/python3 -m ensurepip --upgrade 2>/dev/null || true
  fi
  if ! .venv/bin/python3 -m pip --version &>/dev/null; then
    echo "Bootstrapping pip via get-pip.py..."
    curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python3
  fi
  .venv/bin/python3 -m pip install -q --upgrade pip
  .venv/bin/python3 -m pip install -q -r requirements.txt
else
  .venv/bin/python3 -m pip install -q -r requirements.txt
fi
echo "Restarting svd..."
systemctl restart svd
systemctl status svd --no-pager -l
echo "Done."
