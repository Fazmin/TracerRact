#!/bin/sh
# Create the database tables if needed (existing data is kept), then serve.
set -e
flask --app ip_explorer init-db --if-empty
exec gunicorn --config /app/gunicorn.conf.py 'ip_explorer:create_app()'
