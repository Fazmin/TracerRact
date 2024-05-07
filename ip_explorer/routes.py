"""The page and the JSON API."""
from flask import Blueprint, Response, jsonify, render_template, request

from . import query, store
from .db import get_db
from .facets import FK

bp = Blueprint('explorer', __name__)


def _q():
    return query.Query(get_db(), query.parse_state(request.args))


@bp.get('/')
def index():
    return render_template('index.html')


@bp.get('/healthz')
def healthz():
    get_db().execute('SELECT 1')
    return {'ok': True}


@bp.get('/api/meta')
def meta():
    return query.meta(get_db())


@bp.get('/api/explore')
def explore():
    return _q().explore()


@bp.get('/api/rows')
def rows():
    q = _q()
    return {'rows': q.rows(), 'offset': q.state.offset, 'count': q.kpis()['count']}


@bp.get('/api/facets/<k>')
def facet(k):
    if k not in FK:
        return {'error': 'Unknown facet.'}, 404
    q = _q()
    return q.facet_list(k, q.facet_counts(only=k)[k])


@bp.get('/api/assets/<int:asset_id>')
def asset(asset_id):
    d = query.detail(get_db(), asset_id)
    return d if d else ({'error': 'No such asset.'}, 404)


@bp.get('/api/export.csv')
def export_csv():
    headers = {}
    if request.args.get('download'):
        headers['Content-Disposition'] = 'attachment; filename="ip-assets.csv"'
    return Response(_q().export_csv(), mimetype='text/csv', headers=headers)


@bp.post('/api/import/upload')
def upload():
    kind, f = request.form.get('kind'), request.files.get('file')
    if kind not in ('rz', 'bc') or not f:
        return {'error': 'Send a CSV file and kind=rz or kind=bc.'}, 400
    try:
        return store.stage_upload(get_db(), kind, f.filename or 'upload.csv', f.read())
    except Exception as e:  # anything from a malformed file
        return {'name': f.filename, 'error': f'Could not read this file: {e}'}


@bp.put('/api/import/<int:fid>/map')
def set_map(fid):
    return store.set_map(get_db(), fid, (request.get_json(silent=True) or {}).get('map') or {})


def _ids():
    body = request.get_json(silent=True) or {}
    return body.get('rz') or None, body.get('bc') or None


@bp.post('/api/import/preview')
def preview():
    rz, bc = _ids()
    return store.preview(get_db(), rz, bc)


@bp.post('/api/import/commit')
def commit():
    rz, bc = _ids()
    store.commit(get_db(), rz, bc)
    return query.meta(get_db())


@bp.post('/api/import/reset/<kind>')
def reset(kind):
    if kind not in ('rz', 'bc'):
        return {'error': 'kind must be rz or bc.'}, 400
    store.reset(get_db(), kind)
    return query.meta(get_db())


@bp.errorhandler(ValueError)
def bad_request(e):
    return jsonify(error=str(e)), 400


@bp.app_errorhandler(413)
def too_large(e):
    return jsonify(error='That file is too large to upload.'), 413
