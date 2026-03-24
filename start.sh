#!/bin/sh
# ─────────────────────────────────────────────────────────────────
#  ResumeGrid startup script
#  Runs whatsapp-web.js Node service (background) + Gunicorn (foreground)
#  Railway monitors the foreground process for health.
#
#  COST NOTE: Chromium is NOT launched here — it starts on-demand
#  when the user clicks "Connect WhatsApp" in the UI (/connect route).
# ─────────────────────────────────────────────────────────────────

WA_PORT="${WA_PORT:-3001}"
PORT="${PORT:-5000}"

echo "[start.sh] Starting WhatsApp service on port ${WA_PORT}..."
# Cap Node.js heap at 256 MB — prevents V8 from ballooning before GC kicks in
node --max-old-space-size=256 /app/whatsapp-service/server.js &
WA_PID=$!

# Wait up to 10 seconds for the Express server to be ready
# (Chromium itself does NOT start here — only the Express HTTP server)
echo "[start.sh] Waiting for WhatsApp service HTTP server..."
TRIES=0
until wget -q -O- "http://localhost:${WA_PORT}/health" > /dev/null 2>&1; do
  TRIES=$((TRIES + 1))
  if [ $TRIES -ge 10 ]; then
    echo "[start.sh] WARNING: WhatsApp service did not start in time — continuing anyway"
    break
  fi
  sleep 1
done

if kill -0 "$WA_PID" 2>/dev/null; then
  echo "[start.sh] WhatsApp service is up (PID $WA_PID)"
else
  echo "[start.sh] WARNING: WhatsApp service process has exited — restarting..."
  node --max-old-space-size=256 /app/whatsapp-service/server.js &
fi

echo "[start.sh] Starting Gunicorn on port ${PORT}..."
exec gunicorn app:app --config gunicorn.conf.py