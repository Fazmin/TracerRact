"""Source files (seed, uploaded, staged) and rebuilding the merged asset tables from them.

Per kind ('rz' runZero, 'bc' BlueCat) the effective file is the latest upload ('active') or, when
there is none, the seed CSV ('builtin'). Committing an upload replaces the previous one.
"""
import json
import os
from datetime import datetime, timezone

from . import ingest
from .db import transaction
from .facets import FACETS, RISK_ORDER
from .merge import addresses, merge
from .search import SEARCH_FIELDS, search_row
from .util import ipn

BC_KEYS = list(ingest.BCF)
ASSET_SCALARS = ['hasRz', 'hasBc', 'dup', 'ip', 'n', 'ipSrc', 'host', 'mac', 'vendor', 'bcIp', 'bcHost', 'an', 'owner', 'room',
                 'roomL', 'bldC', 'bld', 'bldSrc', 'sub', 'subName', 'parent', 'state', 'loc', 'type', 'cat', 'os', 'osv',
                 'hw', 'risk', 'alive', 'first', 'last', 'agent', 'det', 'eol', 'serial', 'vlan', 'func', 'crit', 'notes',
                 'cov', 'match', 'blk', 'seenB', 'seenM', 'seenD']
ASSET_LISTS = ['scopeIps', 'ips2', 'hosts', 'macs', 'tcp', 'udp', 'proto', 'prod', 'tags', 'bcMore']
BOOL_FIELDS = {'hasRz', 'hasBc', 'dup'}
# Record keys that are SQL keywords are stored under another column name.
DB_NAME = {'first': 'first_ms', 'last': 'last_ms', 'match': 'match_by'}
REC_NAME = {v: k for k, v in DB_NAME.items()}
HELPER_COLUMNS = {'risk_idx', 'tcp_count', 'tag_count'}


def column(k):
    return DB_NAME.get(k, k)


def _dumps(v):
    return json.dumps(v, ensure_ascii=False)


def record_to_row(r):
    return ([r['id']] + [int(r[k]) if k in BOOL_FIELDS else r[k] for k in ASSET_SCALARS]
            + [_dumps(r[k]) for k in ASSET_LISTS]
            + [_dumps(r['raw']) if r['raw'] is not None else None,
               RISK_ORDER.index(r['risk']) if r['risk'] in RISK_ORDER else 99, len(r['tcp']), len(r['tags'])])


def row_to_record(row):
    """An assets row (any subset of columns) back to the record shape the browser uses."""
    out = {}
    for k in row.keys():
        if k in HELPER_COLUMNS:
            continue
        v = row[k]
        name = REC_NAME.get(k, k)
        if name in BOOL_FIELDS:
            v = bool(v)
        elif name in ASSET_LISTS:
            v = json.loads(v)
        elif name == 'raw':
            v = json.loads(v) if v else None
        out[name] = v
    return out


_ASSET_COLS = ['id'] + [column(k) for k in ASSET_SCALARS] + ASSET_LISTS + ['raw', 'risk_idx', 'tcp_count', 'tag_count']
_INSERT_ASSET = f"INSERT INTO assets ({', '.join(_ASSET_COLS)}) VALUES ({', '.join('?' * len(_ASSET_COLS))})"
_SEARCH_COLS = ['asset_id', 'all_text'] + ['f_' + k for k in SEARCH_FIELDS]
_INSERT_SEARCH = f"INSERT INTO asset_search ({', '.join(_SEARCH_COLS)}) VALUES ({', '.join('?' * len(_SEARCH_COLS))})"
_INSERT_BC = f"INSERT INTO bluecat_rows (file_id, {', '.join(BC_KEYS)}) VALUES ({', '.join('?' * (len(BC_KEYS) + 1))})"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def add_file(db, kind, name, role, headers, rows=None, objs=None, col_map=None):
    cur = db.execute(
        'INSERT INTO source_files (kind, name, role, uploaded_at, headers, column_map, row_count) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (kind, name, role, _now(), _dumps(headers), _dumps(col_map or {}), len(objs if kind == 'bc' else rows)))
    fid = cur.lastrowid
    if kind == 'bc':
        db.executemany(_INSERT_BC, ([fid] + [o[k] for k in BC_KEYS] for o in objs))
    else:
        db.executemany('INSERT INTO runzero_rows (file_id, idx, cells) VALUES (?, ?, ?)',
                       ((fid, i, _dumps(r)) for i, r in enumerate(rows)))
    return fid


def get_file(db, fid):
    return db.execute('SELECT * FROM source_files WHERE id = ?', (fid,)).fetchone()


def effective_file(db, kind):
    return db.execute("SELECT * FROM source_files WHERE kind = ? AND role IN ('active', 'builtin') "
                      "ORDER BY role = 'active' DESC LIMIT 1", (kind,)).fetchone()


def load_bluecat(db, fid):
    return [dict(zip(BC_KEYS, row)) for row in
            db.execute(f"SELECT {', '.join(BC_KEYS)} FROM bluecat_rows WHERE file_id = ? ORDER BY rowid", (fid,))]


def load_runzero(db, frow):
    """(table, column map) for a runZero source_files row."""
    rows = [json.loads(c) for (c,) in db.execute('SELECT cells FROM runzero_rows WHERE file_id = ? ORDER BY idx', (frow['id'],))]
    return {'headers': json.loads(frow['headers']), 'rows': rows}, json.loads(frow['column_map'])


def _parse_rz(text):
    tb = ingest.parse_csv(text)
    if len(tb['headers']) < 2 or not tb['rows']:
        return tb, None
    m = ingest.auto_map(tb['headers'])
    if m['ip'] < 0:
        m['ip'] = ingest.guess_ip_col(tb)
    return tb, m


def seed(db, bluecat_path=None, runzero_path=None):
    """Load CSVs from disk as the built-in files (what "Go back to the built-in" returns to) and build."""
    bc_objs = rz_tb = rz_map = None
    if bluecat_path:
        with open(bluecat_path, 'rb') as f:
            bc = ingest.bluecat_from_table(ingest.parse_csv(ingest.decode(f.read())))
        if 'error' in bc:
            raise ValueError(f'{bluecat_path}: {bc["error"]}')
        bc_objs = bc['objs']
    if runzero_path:
        with open(runzero_path, 'rb') as f:
            rz_tb, rz_map = _parse_rz(ingest.decode(f.read()))
        if rz_map is None:
            raise ValueError(f'{runzero_path}: This file has no data rows.')
    with transaction(db):
        if bc_objs is not None:
            add_file(db, 'bc', os.path.basename(bluecat_path), 'builtin', [], objs=bc_objs)
        if rz_tb:
            add_file(db, 'rz', os.path.basename(runzero_path), 'builtin', rz_tb['headers'], rows=rz_tb['rows'], col_map=rz_map)
        total = rebuild(db)
    return {'bc': len(bc_objs or []), 'rz': len(rz_tb['rows']) if rz_tb else 0, 'total': total}


def stage_upload(db, kind, name, data):
    """Parse an uploaded CSV and keep it as a staged file. Returns what the import dialog shows."""
    text = ingest.decode(data)
    if kind == 'rz':
        tb, m = _parse_rz(text)
        if m is None:
            return {'name': name, 'error': 'This file has no data rows.'}
        with transaction(db):
            db.execute("DELETE FROM source_files WHERE kind = 'rz' AND role = 'staged'")
            fid = add_file(db, 'rz', name, 'staged', tb['headers'], rows=tb['rows'], col_map=m)
        return {'uploadId': fid, 'name': name, 'rows': len(tb['rows']), 'headers': tb['headers'], 'map': m,
                'samples': ingest.column_samples(tb), 'ipStats': ingest.ip_stats(tb, m['ip'])}
    res = ingest.bluecat_from_table(ingest.parse_csv(text))
    if 'error' in res:
        return {'name': name, 'error': res['error']}
    with transaction(db):
        db.execute("DELETE FROM source_files WHERE kind = 'bc' AND role = 'staged'")
        fid = add_file(db, 'bc', name, 'staged', [], objs=res['objs'])
    return {'uploadId': fid, 'name': name, 'count': len(res['objs'])}


def _staged(db, fid, kind):
    f = get_file(db, fid)
    if not f or f['kind'] != kind or f['role'] != 'staged':
        raise ValueError('That upload is no longer available. Choose the file again.')
    return f


def set_map(db, fid, col_map):
    f = _staged(db, fid, 'rz')
    n = len(json.loads(f['headers']))
    clean = {}
    for k in ingest.RZF_KEYS:
        v = int(col_map.get(k, -1))
        clean[k] = v if -1 <= v < n else -1
    db.execute('UPDATE source_files SET column_map = ? WHERE id = ?', (_dumps(clean), fid))
    tb, _ = load_runzero(db, f)
    return {'map': clean, 'ipStats': ingest.ip_stats(tb, clean['ip'])}


def _sources(db, rz_id=None, bc_id=None):
    bc = _staged(db, bc_id, 'bc') if bc_id else effective_file(db, 'bc')
    rz = _staged(db, rz_id, 'rz') if rz_id else effective_file(db, 'rz')
    bc_objs = load_bluecat(db, bc['id']) if bc else []
    rz_tb, rz_map = load_runzero(db, rz) if rz else (None, {})
    return bc_objs, rz_tb, rz_map


def preview(db, rz_id=None, bc_id=None):
    """Dry-run merge of staged uploads with the current files of the other kind."""
    recs, stats = merge(*_sources(db, rz_id, bc_id))
    return {'stats': stats, 'total': len(recs)}


def commit(db, rz_id=None, bc_id=None):
    with transaction(db):
        for kind, fid in (('rz', rz_id), ('bc', bc_id)):
            if fid:
                _staged(db, fid, kind)
                db.execute("DELETE FROM source_files WHERE kind = ? AND role = 'active'", (kind,))
                db.execute("UPDATE source_files SET role = 'active' WHERE id = ?", (fid,))
        return rebuild(db)


def reset(db, kind):
    """Drop the uploaded file of this kind and go back to the seed CSV."""
    with transaction(db):
        db.execute("DELETE FROM source_files WHERE kind = ? AND role IN ('active', 'staged')", (kind,))
        return rebuild(db)


def rebuild(db):
    """Re-merge the effective files into assets, asset_facets, asset_search, asset_ips and meta. Call inside a transaction."""
    recs, stats = merge(*_sources(db))
    for t in ('assets', 'asset_facets', 'asset_search', 'asset_ips', 'meta'):
        db.execute(f'DELETE FROM {t}')
    db.executemany(_INSERT_ASSET, (record_to_row(r) for r in recs))
    db.executemany('INSERT INTO asset_facets (asset_id, facet, value) VALUES (?, ?, ?)',
                   ((r['id'], f.k, v) for r in recs for f in FACETS for v in f.values(r)))
    db.executemany(_INSERT_SEARCH, ([r['id']] + search_row(r) for r in recs))
    db.executemany('INSERT INTO asset_ips (asset_id, n) VALUES (?, ?)',
                   ((r['id'], n) for r in recs for n in {ipn(x) for x in addresses(r)} if n >= 0))
    db.execute("INSERT INTO meta (key, value) VALUES ('stats', ?)", (_dumps(stats),))
    return len(recs)


def upgrade(db):
    """Bring a database built by an older version up to date: map runZero fields added since its files were
    uploaded (by column name, keeping every stored choice), then re-merge. Call inside a transaction."""
    for f in db.execute("SELECT id, headers, column_map FROM source_files WHERE kind = 'rz'").fetchall():
        m = {**ingest.auto_map(json.loads(f['headers'])), **json.loads(f['column_map'])}
        db.execute('UPDATE source_files SET column_map = ? WHERE id = ?', (_dumps(m), f['id']))
    return rebuild(db)
