#!/bin/sh
# ─────────────────────────────────────────────────────────────────
#  ResumeGrid startup script
#  Runs whatsapp-web.js Node service (background) + Gunicorn (foreground)
#  Railway monitors the foreground process for health.
# ─────────────────────────────────────────────────────────────────

# NOTE: Do NOT use "set -e" here — if Node crashes, we don't want
# the whole container to die. Flask will report WA as disconnected,
# which is recoverable. "set -e" would kill Gunicorn too.

WA_PORT="${WA_PORT:-3001}"
PORT="${PORT:-5000}"

echo "[start.sh] Starting WhatsApp service on port ${WA_PORT}..."
node /app/whatsapp-service/server.js &
WA_PID=$!

# Wait up to 15 seconds for the Node service to be ready
echo "[start.sh] Waiting for WhatsApp service to come up..."
TRIES=0
until wget -q -O- "http://localhost:${WA_PORT}/health" > /dev/null 2>&1; do
  TRIES=$((TRIES + 1))
  if [ $TRIES -ge 15 ]; then
    echo "[start.sh] WARNING: WhatsApp service did not start in time — continuing anyway"
    break
  fi
  sleep 1
done

if kill -0 "$WA_PID" 2>/dev/null; then
  echo "[start.sh] WhatsApp service is up (PID $WA_PID)"
else
  echo "[start.sh] WARNING: WhatsApp service process has exited — restarting..."
  node /app/whatsapp-service/server.js &
fi

echo "[start.sh] Starting Gunicorn on port ${PORT}..."
exec gunicorn app:app --config gunicorn.conf.py