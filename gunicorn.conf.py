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
worker_connections   = 20          # reduced from 50 — sequential uploads don't need more
timeout              = 300
keepalive            = 2           # reduced from 5 — fewer idle keep-alive sockets
loglevel             = "info"

# Recycle worker more aggressively to free PDF/openpyxl memory buildup
max_requests         = 200         # was 500 — recycles sooner after batch processing
max_requests_jitter  = 25          # was 50

# Load app code once, then fork — saves ~30-50 MB per-worker import overhead
preload_app          = True