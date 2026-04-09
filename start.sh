#!/bin/sh
# ─────────────────────────────────────────────────────────────────
#  ResumeGrid startup script  [cost-optimised]
#
#  Changes vs previous version:
#   • Node heap cap reduced from 256 MB → 192 MB. The WA service's idle
#     footprint (Express + whatsapp-web.js library, NO Chromium) is ~60-80 MB.
#     192 MB gives ample headroom for bursts while preventing over-allocation.
#   • Added --expose-gc and --gc-interval=100 so V8 collects more aggressively
#     in the idle periods between WhatsApp sessions.
# ─────────────────────────────────────────────────────────────────

PORT="${PORT:-5000}"

echo "[start.sh] Starting WhatsApp service on port ${WA_PORT}..."
node \
  --max-old-space-size=192 \
  --expose-gc \
  --gc-interval=100 \
  /app/whatsapp-service/server.js &
WA_PID=$!

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
  node \
    --max-old-space-size=192 \
    --expose-gc \
    --gc-interval=100 \
    /app/whatsapp-service/server.js &
fi

echo "[start.sh] Starting Gunicorn on port ${PORT}..."
exec gunicorn app:app --config gunicorn.conf.py