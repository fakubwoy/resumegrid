#!/bin/sh
# ─────────────────────────────────────────────────────────────────
#  ResumeGrid startup script
#  Runs whatsapp-web.js Node service (background) + Gunicorn (foreground)
#  Railway monitors the foreground process for health.
# ─────────────────────────────────────────────────────────────────

set -e

echo "[start.sh] Starting WhatsApp service on port ${WA_PORT:-3001}..."
node /app/whatsapp-service/server.js &
WA_PID=$!

echo "[start.sh] Starting Gunicorn on port ${PORT:-5000}..."
exec gunicorn app:app --config gunicorn.conf.py