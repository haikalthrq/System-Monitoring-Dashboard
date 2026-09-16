#!/bin/bash
# System Monitor - Start Script
# Usage: ./start.sh [--port 9090] [--host 0.0.0.0]
# Env: PORT=9090 HOST=0.0.0.0

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-9090}"
HOST="${HOST:-0.0.0.0}"

# Find python
if command -v python3 &>/dev/null; then
  PY=python3
elif command -v python &>/dev/null; then
  PY=python
else
  echo "python3 not found"
  exit 1
fi

# Check if port in use
if ss -tln 2>/dev/null | grep -q ":$PORT "; then
  echo "Port $PORT already in use, trying next..."
  for p in $(seq $((PORT+1)) $((PORT+10))); do
    if ! ss -tln 2>/dev/null | grep -q ":$p "; then
      PORT=$p
      echo "Using port $PORT"
      break
    fi
  done
fi

echo "Starting System Monitor on http://$HOST:$PORT"
echo "  Dashboard: http://localhost:$PORT/"
echo "  API:       http://localhost:$PORT/api/stats"
echo "  Stream:    http://localhost:$PORT/api/stream"
exec $PY "$DIR/app.py" --host "$HOST" --port "$PORT" "$@"
