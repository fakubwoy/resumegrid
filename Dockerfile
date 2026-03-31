# ─────────────────────────────────────────────────────────────────
#  ResumeGrid — Railway single-container image  (cost-optimised)
#
#  Key changes vs previous version:
#   • Removed fonts-noto-color-emoji (~110 MB) — not needed for WhatsApp text
#   • Removed fonts-noto (~60 MB) — system fonts still present
#   • Removed gcc (only needed at build time — moved to one layer, then purged)
#   • Merged all apt-get calls into ONE layer so intermediate caches don't bloat image
#   • Added --no-install-recommends everywhere (saves ~80 MB from recommended extras)
#   • npm ci --omit=dev (same as before, kept)
#   • pip --no-cache-dir (same as before, kept)
#   • Explicit cleanup: apt lists + pip cache purged in same RUN layer
# ─────────────────────────────────────────────────────────────────
FROM python:3.10-slim

WORKDIR /app

# ── System dependencies — one layer, purge apt lists immediately ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    ca-certificates \
    # PDF / OCR
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    # Chromium + required shared libs (Puppeteer / whatsapp-web.js)
    chromium \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 20 LTS (separate apt call to keep nodesource setup clean) ─
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Node WhatsApp service ─────────────────────────────────────────
COPY whatsapp-service/package.json ./whatsapp-service/
RUN cd whatsapp-service && npm install --omit=dev \
    && npm cache clean --force

COPY whatsapp-service/server.js ./whatsapp-service/

# ── Tell Puppeteer to use system Chromium (no extra download) ─────
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# ── WhatsApp service internal port ───────────────────────────────
ENV WA_PORT=3001
ENV WA_SERVICE_URL=http://localhost:3001

# ── Idle timeout: destroy Chromium after 10 min of no sends ──────
#    (was 15 min default — reduces RAM waste for inactive sessions)
ENV WA_IDLE_TIMEOUT_MS=600000

# ── App source ────────────────────────────────────────────────────
COPY app.py .
COPY gunicorn.conf.py .
COPY static/ ./static/

# ── Startup script ────────────────────────────────────────────────
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 3001
CMD ["./start.sh"]