# Gunicorn configuration — loaded automatically by gunicorn regardless of how it's invoked
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
worker_class = "gevent"
workers = 2
timeout = 300
keepalive = 5
loglevel = "info"