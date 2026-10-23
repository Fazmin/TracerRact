import os

bind = '0.0.0.0:8000'
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
threads = 4
# Rebuilding after a large upload can take a while.
timeout = 120


def post_worker_init(worker):
    # Every worker offers to run the IP tracker's checks; a lease lets one at a time do it (scheduler.py).
    import logging

    from ip_explorer import scheduler

    log = logging.getLogger('ip_explorer')
    log.handlers = worker.log.error_log.handlers
    log.setLevel(logging.INFO)
    scheduler.start(worker.wsgi)


def worker_exit(server, worker):
    from ip_explorer import scheduler

    scheduler.release(worker.wsgi)


def when_ready(server):
    port = os.environ.get('HOST_PORT', '8080')
    server.log.info('IP Asset Explorer is ready. Open http://localhost:%s in your browser.', port)
