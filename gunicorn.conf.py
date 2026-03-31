# Gunicorn configuration — loaded automatically regardless of how gunicorn is invoked
#
# Cost optimisations vs previous version:
#   • max_requests / max_requests_jitter: worker recycles after 500 requests.
#     This prevents memory creep from pdfplumber/openpyxl object retention
#     between batches, which was slowly growing RSS over time.
#   • worker_connections reduced from 100 → 50: the app is not a high-concurrency
#     API — resume batches are sequential. 50 green threads is more than enough
#     and reduces gevent's per-worker overhead.
#   • preload_app=True: the Python interpreter + all imports are loaded ONCE and
#     then fork()ed into the worker. Saves ~30-50 MB vs loading them per-worker.

import os

bind                 = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
worker_class         = "gevent"
workers              = 1           # gevent handles concurrency; 1 worker is enough
worker_connections   = 50          # reduced from 100 — resume uploads are sequential
timeout              = 300
keepalive            = 5
loglevel             = "info"

# Recycle the worker periodically to free accumulated memory from PDF processing
max_requests         = 500
max_requests_jitter  = 50          # randomise so all workers don't restart together

# Load app code once, then fork — saves ~30-50 MB per-worker import overhead
preload_app          = True