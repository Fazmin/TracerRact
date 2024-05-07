"""IP Asset Explorer: BlueCat IPAM and runZero scan data, merged and searchable."""
import os

from flask import Flask


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get('DATABASE') or os.path.join(app.instance_path, 'explorer.sqlite'),
        # Optional CSVs that init-db loads as built-in data. Normally data arrives by upload.
        SEED_BLUECAT=os.environ.get('SEED_BLUECAT') or None,
        SEED_RUNZERO=os.environ.get('SEED_RUNZERO') or None,
        MAX_CONTENT_LENGTH=200 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(os.path.dirname(os.path.abspath(app.config['DATABASE'])), exist_ok=True)
    # Keep runZero's column order in the asset detail panel.
    app.json.sort_keys = False
    app.json.ensure_ascii = False

    from . import db, routes
    db.init_app(app)
    app.register_blueprint(routes.bp)
    return app
