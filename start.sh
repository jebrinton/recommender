#!/usr/bin/env bash
# start.sh — launch the Recommender web server on your Mac
# Run once from your terminal: bash start.sh
# Then open: http://localhost:7432

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=7432

# ── Already running? ──────────────────────────────────────────────────────────
if curl -sf "http://localhost:$PORT/api/runs" > /dev/null 2>&1; then
  echo "✓  Recommender already running → http://localhost:$PORT"
  exit 0
fi

# ── Install Python dependencies if missing ────────────────────────────────────
echo "Checking dependencies…"
python3 -c "import fastapi, uvicorn" 2>/dev/null || {
  echo "Installing fastapi + uvicorn…"
  pip3 install fastapi "uvicorn[standard]" --break-system-packages -q
}

# ── Launch server in background ───────────────────────────────────────────────
cd "$DIR"
nohup python3 server.py > server.log 2>&1 &
echo $! > server.pid
echo "Starting…"

# Wait up to 5 s for the server to come up
for i in $(seq 1 10); do
  sleep 0.5
  if curl -sf "http://localhost:$PORT/api/runs" > /dev/null 2>&1; then
    echo "✓  Recommender started → http://localhost:$PORT"
    exit 0
  fi
done

echo "✗  Server may not have started — check server.log"
exit 1
