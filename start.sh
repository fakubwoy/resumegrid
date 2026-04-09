#!/bin/sh
# ─────────────────────────────────────────────────────────────────
#  ResumeGrid startup script  [lazy WhatsApp boot]
#
#  The WhatsApp / Node / Chromium service is NOT started here.
#  It is spawned on-demand by Flask the first time the user hits
#  /wa/connect, and destroyed automatically after WA_IDLE_TIMEOUT_MS
#  of inactivity (default: 10 min).
# ─────────────────────────────────────────────────────────────────

PORT="${PORT:-5000}"

echo "[start.sh] Starting Gunicorn on port ${PORT}..."
exec gunicorn app:app --config gunicorn.conf.py