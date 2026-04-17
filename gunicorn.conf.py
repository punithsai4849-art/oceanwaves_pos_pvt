# -----------------------------------------------------------------------------
# GUNICORN CONFIG - Optimized for 1GB RAM
# -----------------------------------------------------------------------------
import multiprocessing

# On 1GB RAM, we use 1 worker with multiple threads.
# This handles concurrency without multiplying the Django memory usage.
workers = 1
threads = 4
worker_class = 'gthread'

# Bind to localhost (Nginx will proxy to this)
bind = '127.0.0.1:8000'

# Timeouts
timeout = 120
keepalive = 5

# Logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'
