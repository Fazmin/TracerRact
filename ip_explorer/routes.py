"""The page and the JSON API."""
from flask import Blueprint, Response, jsonify, render_template, request

from . import query, store, tracker
from .db import get_db
from .facets import FK
from .util import ipn

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
    if not d:
        return {'error': 'No such asset.'}, 404
    d['tracking'] = tracker.for_ip(get_db(), d['asset']['ip'])
    return d


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


# -- IP tracker --------------------------------------------------------------------------------
def _body():
    return request.get_json(silent=True) or {}


def _tracker_list_args():
    a = request.args
    return {'bucket': a.get('bucket') or '', 'q': (a.get('q') or '').strip(), 'sort': a.get('sort') or 'ip',
            'direction': -1 if a.get('dir') == '-1' else 1}


@bp.get('/api/tracker')
def tracker_summary():
    return tracker.summary(get_db())


@bp.get('/api/tracker/targets')
def tracker_targets():
    a = request.args
    return tracker.targets(get_db(), offset=max(0, query._int(a.get('offset'), 0)),
                           limit=min(max(1, query._int(a.get('limit'), query.PAGE)), tracker.MAX_ADD), **_tracker_list_args())


@bp.post('/api/tracker/targets')
def tracker_add():
    b = _body()
    nums, bad = tracker.parse_targets(b.get('ips'))
    if not nums:
        return {'error': 'Enter IP addresses, CIDR blocks such as 10.1.2.0/24, or ranges such as 10.1.2.5-10.1.2.40.',
                'invalid': bad}, 400
    return {**tracker.add(get_db(), nums, b.get('every') or tracker.DEFAULT_EVERY, b.get('label')), 'invalid': bad}


@bp.post('/api/tracker/from-explorer')
def tracker_from_explorer():
    """Track the primary address of every asset matching the explorer filters in the query string."""
    b = _body()
    sql, p = _q().where()
    nums = sorted({n for (ip,) in get_db().execute(f'SELECT DISTINCT a.ip FROM {sql}', p) if (n := ipn(ip)) >= 0})
    if not nums:
        return {'error': 'None of these assets has an IPv4 address.'}, 400
    return tracker.add(get_db(), nums, b.get('every') or tracker.DEFAULT_EVERY, b.get('label'))


@bp.patch('/api/tracker/targets')
def tracker_update():
    b = _body()
    return tracker.update(get_db(), b.get('ids'), every=b.get('every'), paused=b.get('paused'), label=b.get('label'))


@bp.delete('/api/tracker/targets')
def tracker_remove():
    return tracker.remove(get_db(), _body().get('ids'))


@bp.post('/api/tracker/check')
def tracker_check():
    db = get_db()
    return tracker.check_now(db, _body().get('ids'), tracker.prober(db))


@bp.get('/api/tracker/targets/<int:tid>')
def tracker_profile(tid):
    p = tracker.profile(get_db(), tid)
    return p if p else ({'error': 'That address is no longer tracked.'}, 404)


@bp.get('/api/tracker/export.csv')
def tracker_export():
    headers = {}
    if request.args.get('download'):
        headers['Content-Disposition'] = 'attachment; filename="tracked-ips.csv"'
    return Response(tracker.export_csv(get_db(), **_tracker_list_args()), mimetype='text/csv', headers=headers)


@bp.errorhandler(ValueError)
def bad_request(e):
    return jsonify(error=str(e)), 400


@bp.app_errorhandler(413)
def too_large(e):
    return jsonify(error='That file is too large to upload.'), 413
