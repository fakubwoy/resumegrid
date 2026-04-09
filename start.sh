#!/bin/sh
# ─────────────────────────────────────────────────────────────────
#  ResumeGrid local startup script
#  Starts WhatsApp Node service (optional) + Flask
# ─────────────────────────────────────────────────────────────────

WA_PORT="${WA_PORT:-3001}"
PORT="${PORT:-5000}"

# ── Optional: start WhatsApp service if server.js exists ─────────
if [ -f "/app/whatsapp-service/server.js" ]; then
  echo "[start.sh] Starting WhatsApp service on port ${WA_PORT}..."
  node \
    --max-old-space-size=192 \
    --expose-gc \
    --gc-interval=100 \
    /app/whatsapp-service/server.js &
  WA_PID=$!

  TRIES=0
  until wget -q -O- "http://localhost:${WA_PORT}/health" > /dev/null 2>&1; do
    TRIES=$((TRIES + 1))
    if [ $TRIES -ge 10 ]; then
      echo "[start.sh] WARNING: WhatsApp service did not start in time — continuing anyway"
      break
    fi
    sleep 1
  done
  echo "[start.sh] WhatsApp service up (PID $WA_PID)"
else
  echo "[start.sh] No WhatsApp service found — skipping (WhatsApp outreach will be unavailable)"
fi

# ── Verify Ollama is reachable ────────────────────────────────────
OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
echo "[start.sh] Checking Ollama at ${OLLAMA_URL}..."
if wget -q -O- "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
  echo "[start.sh] Ollama is running ✓"
else
  echo "[start.sh] WARNING: Ollama not reachable at ${OLLAMA_URL}"
  echo "[start.sh]   Make sure Ollama is running: ollama serve"
  echo "[start.sh]   And the model is pulled: ollama pull mistral-nemo"
fi

# ── Start Flask ───────────────────────────────────────────────────
echo "[start.sh] Starting Flask on port ${PORT}..."
exec python app.py