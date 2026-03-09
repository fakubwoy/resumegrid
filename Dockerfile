FROM python:3.10-slim

WORKDIR /app

# gcc needed for gevent's C extension
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY static/ ./static/

EXPOSE 5000

# gevent workers: SSE streams + health checks run concurrently, no blocking
# timeout 600s covers large batches with rate-limit retries
CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT:-5000} \
    --worker-class gevent \
    --workers 2 \
    --worker-connections 100 \
    --timeout 600 \
    --keep-alive 5