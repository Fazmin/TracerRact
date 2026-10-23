import os

bind = '0.0.0.0:8000'
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
threads = 4
# Rebuilding after a large upload can take a while.
timeout = 120


def when_ready(server):
    port = os.environ.get('HOST_PORT', '8080')
    server.log.info('IP Asset Explorer is ready. Open http://localhost:%s in your browser.', port)
