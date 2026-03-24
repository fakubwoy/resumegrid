# Gunicorn configuration — loaded automatically by gunicorn regardless of how it's invoked
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
worker_class = "gevent"
workers = 1          # was 2 — gevent handles concurrency; 2 workers = 2x RAM for no benefit
worker_connections = 100  # gevent green threads per worker (default 10 is too low for bulk uploads)
timeout = 300
keepalive = 5
loglevel = "info"