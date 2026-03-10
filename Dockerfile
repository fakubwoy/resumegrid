FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ ./static/

EXPOSE 5000

# gevent worker: handles long-lived SSE streams without blocking other requests
# timeout 300: allows up to 5 min per request (enough for ~20 resumes at ~10s each)
# workers 2: allows one request to process while another serves health checks
CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT:-5000} \
    --worker-class gevent \
    --workers 2 \
    --timeout 300 \
    --keep-alive 5 \
    --log-level info