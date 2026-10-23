"""SQLite connection handling and the init-db command."""
import json
import os
import sqlite3
from contextlib import contextmanager

import click
from flask import current_app, g
from flask.cli import with_appcontext

from .util import natural_cmp


def _raw_get(raw, key):
    if not raw:
        return ''
    return json.loads(raw).get(key, '')


def connect(path):
    # Autocommit mode; writes are grouped explicitly with transaction().
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.create_collation('NATURAL', natural_cmp)
    conn.create_function('raw_get', 2, _raw_get, deterministic=True)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


def get_db():
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE'])
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        db.execute('ROLLBACK')
        raise
    db.execute('COMMIT')


def has_data(db):
    try:
        return db.execute("SELECT 1 FROM source_files WHERE role IN ('builtin', 'active') LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        return False


def has_targets(db):
    try:
        return db.execute('SELECT 1 FROM track_targets LIMIT 1').fetchone() is not None
    except sqlite3.OperationalError:
        return False


TABLES = ['track_checks', 'track_targets', 'tracker_state',
          'runzero_rows', 'bluecat_rows', 'source_files', 'asset_facets', 'asset_search', 'asset_ips', 'assets', 'meta']
# Rebuilt from the source files on every import.
MERGED_TABLES = ['asset_facets', 'asset_search', 'asset_ips', 'assets', 'meta']
# Bump when the merged tables or the merge change, so a database built by another version is
# rebuilt from its source files on the next start.
BUILD_VERSION = 2


def _build_version(db):
    return db.execute('PRAGMA user_version').fetchone()[0]


def init_schema(db, reset=False):
    """Create any missing tables; with reset, drop everything first. A database built by another
    version keeps its source files and has its merged tables recreated and rebuilt."""
    if reset or _build_version(db) != BUILD_VERSION:
        for t in TABLES if reset else MERGED_TABLES:
            db.execute(f'DROP TABLE IF EXISTS {t}')
    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf-8'))
    db.execute('PRAGMA journal_mode = WAL')
    if _build_version(db) != BUILD_VERSION:
        from . import store

        with transaction(db):
            store.upgrade(db)
            db.execute(f'PRAGMA user_version = {BUILD_VERSION}')


@click.command('init-db')
@click.option('--bluecat', type=click.Path(dir_okay=False), help='BlueCat export CSV to load as built-in data (default: SEED_BLUECAT).')
@click.option('--runzero', type=click.Path(dir_okay=False), help='runZero asset CSV to load as built-in data (default: SEED_RUNZERO).')
@click.option('--if-empty', is_flag=True, help='Do nothing when the database already has data or tracked IPs.')
@with_appcontext
def init_db_command(bluecat, runzero, if_empty):
    """Reset the database, optionally loading CSVs as built-in data. Without CSVs it starts empty."""
    from . import store

    db = get_db()
    # Tracked IPs count as data: someone may use only the tracker, and this runs on every start.
    if if_empty and (has_data(db) or has_targets(db)):
        click.echo('Database already has data; keeping it.')
        return
    bluecat = bluecat or current_app.config['SEED_BLUECAT']
    runzero = runzero or current_app.config['SEED_RUNZERO']
    for path in (bluecat, runzero):
        if path and not os.path.exists(path):
            raise click.ClickException(f'File not found: {path}')
    init_schema(db, reset=True)
    if not bluecat and not runzero:
        click.echo('Created an empty database. Upload BlueCat and runZero exports from the app.')
        return
    summary = store.seed(db, bluecat, runzero)
    click.echo(f"Loaded {summary['bc']} BlueCat addresses and {summary['rz']} runZero assets "
               f"({summary['total']} merged records).")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    # Tables must exist before anything is uploaded; this is a no-op once they do.
    with app.app_context():
        init_schema(get_db())
