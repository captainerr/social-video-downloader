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
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
else
  .venv/bin/pip install -q -r requirements.txt
fi
echo "Restarting svd..."
systemctl restart svd
systemctl status svd --no-pager -l
echo "Done."
