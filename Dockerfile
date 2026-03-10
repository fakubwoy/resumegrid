# ─────────────────────────────────────────────────────────────────
#  ResumeGrid — Railway single-container image
#  • Python 3.10 (Flask + Gunicorn) on $PORT  (Railway-assigned)
#  • Node 20 (whatsapp-web.js) on internal port 3001
#  • Chromium installed for Puppeteer (whatsapp-web.js)
# ─────────────────────────────────────────────────────────────────
FROM python:3.10-slim

WORKDIR /app

# ── System dependencies ────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    gcc \
    curl \
    gnupg \
    ca-certificates \
    # PDF / OCR
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    # Chromium + deps (for Puppeteer inside whatsapp-web.js)
    chromium \
    fonts-noto \
    fonts-noto-color-emoji \
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

# ── Node.js 20 LTS ────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Node WhatsApp service ─────────────────────────────────────────
COPY whatsapp-service/package.json ./whatsapp-service/
RUN cd whatsapp-service && npm install --omit=dev
COPY whatsapp-service/server.js ./whatsapp-service/

# ── Tell Puppeteer to use system Chromium (no extra download) ─────
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# ── WhatsApp service runs on fixed internal port 3001 ─────────────
ENV WA_PORT=3001
# Flask proxy knows where to find it
ENV WA_SERVICE_URL=http://localhost:3001

# ── App source ────────────────────────────────────────────────────
COPY app.py .
COPY gunicorn.conf.py .
COPY static/ ./static/

# ── Startup script ────────────────────────────────────────────────
COPY start.sh .
RUN chmod +x start.sh

# Railway injects $PORT at runtime — Gunicorn reads it via gunicorn.conf.py
EXPOSE 3001
CMD ["./start.sh"]