FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# No data is baked into the image: BlueCat and runZero exports are uploaded in the app.
COPY ip_explorer ./ip_explorer
COPY docker/gunicorn.conf.py ./gunicorn.conf.py
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN useradd --system --uid 10001 --home-dir /app explorer \
    && mkdir -p /data && chown explorer /data \
    && chmod 755 /usr/local/bin/entrypoint.sh
USER explorer

ENV DATABASE=/data/explorer.sqlite \
    HOST_PORT=8080

# SQLite database and uploaded files live here; mount a volume to keep them.
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
ENTRYPOINT ["entrypoint.sh"]
