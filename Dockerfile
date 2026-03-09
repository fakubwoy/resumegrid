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

# gthread: built into gunicorn, handles concurrent SSE + other requests
# threads 10 = up to 10 concurrent requests per worker
# timeout 0 = never kill a worker (SSE streams are long-lived by design)
CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT:-5000} \
    --worker-class gthread \
    --workers 1 \
    --threads 10 \
    --timeout 0 \
    --keep-alive 5