"""Server-side filtering, facet counts, KPIs, charts, locations, asset detail and CSV export.

The filter state is the search text plus selected values per facet. Values inside a facet are OR'd,
facets are AND'd. A selected facet's own counts ignore its own selection, so you can see what else
you could add, as in the original explorer.
"""
import csv
import io
import json
from dataclasses import dataclass, field

from .columns import BASE_COLUMNS, all_columns, order_by
from .db import has_data
from .facets import COV_ORDER, FACETS, FK, RISK_ORDER
from .ingest import RZF
from .search import compile_search
from .store import column, effective_file, row_to_record
from .util import natural_key

PAGE = 200
FACET_LIMIT, FACET_MORE_LIMIT = 10, 400
MAX_ROWS = 1_000_000


@dataclass
class State:
    q: str = ''
    sel: dict = field(default_factory=dict)      # facet key -> list of values
    sort: str = 'n'
    dir: int = 1
    offset: int = 0
    limit: int | None = PAGE                     # None = all
    view: str = 'table'
    charts: bool = True
    more: set = field(default_factory=set)       # facets showing up to FACET_MORE_LIMIT values
    fq: dict = field(default_factory=dict)       # facet key -> text in that facet's find box
    cols: list = field(default_factory=list)     # visible column keys (default columns when empty)


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_state(args):
    """Query string (werkzeug MultiDict) to State."""
    sel = {}
    for f in FACETS:
        vals = list(dict.fromkeys(args.getlist('f.' + f.k)))
        if vals:
            sel[f.k] = vals
    limit = args.get('limit')
    return State(
        q=(args.get('q') or '').strip(),
        sel=sel,
        sort=args.get('sort') or 'n',
        dir=-1 if args.get('dir') == '-1' else 1,
        offset=max(0, _int(args.get('offset'), 0)),
        limit=None if limit == 'all' else min(max(1, _int(limit, PAGE)), MAX_ROWS),
        view='loc' if args.get('view') == 'loc' else 'table',
        charts=args.get('charts') != '0',
        more={k for k in args.getlist('more') if k in FK},
        fq={f.k: args.get('fq.' + f.k) for f in FACETS if args.get('fq.' + f.k)},
        cols=args.getlist('cols'),
    )


class Query:
    def __init__(self, db, state):
        self.db = db
        self.state = state
        self.search = compile_search(state.q)
        self.columns = {c.k: c for c in all_columns(db)}

    # -- filtering -------------------------------------------------------------------------
    def where(self, exclude=None, extra=None):
        """FROM ... WHERE ... for the filtered set, optionally ignoring one facet's selection."""
        clauses, params = [], []
        if self.search:
            clauses.append(self.search[0])
            params += self.search[1]
        for k, vals in self.state.sel.items():
            if k == exclude:
                continue
            clauses.append(f"a.id IN (SELECT asset_id FROM asset_facets WHERE facet = ? AND value IN ({', '.join('?' * len(vals))}))")
            params += [k, *vals]
        if extra:
            clauses.append(extra)
        src = 'assets a JOIN asset_search s ON s.asset_id = a.id' if self.search else 'assets a'
        return f"{src} WHERE {' AND '.join(clauses) or '1'}", params

    def ids(self, exclude=None, extra=None):
        sql, params = self.where(exclude, extra)
        return f'SELECT a.id FROM {sql}', params

    def _pairs(self, sql, params):
        return [(r[0], r[1]) for r in self.db.execute(sql, params)]

    # -- facets ----------------------------------------------------------------------------
    def facet_counts(self, only=None):
        """{facet: {value: count}} for every facet (or just `only`)."""
        active = [k for k in self.state.sel if only in (None, k)]
        wanted = [f.k for f in FACETS if only in (None, f.k) and f.k not in active]
        counts = {k: {} for k in ([only] if only else FK)}
        if wanted:
            ids, p = self.ids()
            sql = (f"SELECT facet, value, COUNT(*) FROM asset_facets WHERE asset_id IN ({ids}) "
                   f"AND facet IN ({', '.join('?' * len(wanted))}) GROUP BY facet, value")
            for fk, v, n in self.db.execute(sql, p + wanted):
                counts[fk][v] = n
        for k in active:
            ids, p = self.ids(exclude=k)
            counts[k] = dict(self._pairs(f'SELECT value, COUNT(*) FROM asset_facets WHERE facet = ? AND asset_id IN ({ids}) GROUP BY value', [k] + p))
        return counts

    def facet_list(self, k, counts):
        """Ordered values for one facet's list, trimmed to what the rail shows."""
        f, selected = FK[k], self.state.sel.get(k, [])
        items = list(counts.items()) + [(v, 0) for v in selected if v not in counts]
        if f.order:
            items.sort(key=lambda i: (f.order.index(i[0]) if i[0] in f.order else 99, -i[1], natural_key(i[0])))
        elif f.num:
            items.sort(key=lambda i: (-i[1], _num(i[0]), natural_key(i[0])))
        else:
            items.sort(key=lambda i: (i[0].startswith('('), -i[1], natural_key(i[0])))
        fq = (self.state.fq.get(k) or '').lower()
        if fq:
            items = [i for i in items if fq in i[0].lower() or i[0] in selected]
        shown = items[:FACET_MORE_LIMIT if k in self.state.more else FACET_LIMIT]
        shown_vals = {i[0] for i in shown}
        shown += [i for i in items if i[0] in selected and i[0] not in shown_vals]
        return {'items': [list(i) for i in shown], 'total': len(items), 'hidden': len(items) - len(shown)}

    def facets(self):
        counts = self.facet_counts()
        return {f.k: self.facet_list(f.k, counts[f.k]) for f in FACETS}

    # -- summary ---------------------------------------------------------------------------
    def kpis(self):
        sql, p = self.where()
        r = self.db.execute(
            "SELECT COUNT(*), TOTAL(a.cov = 'Both'), TOTAL(a.cov = 'runZero only'), "
            "TOTAL(a.cov NOT IN ('Both', 'runZero only')), COUNT(DISTINCT CASE WHEN a.room <> '' THEN a.roomL END), "
            "TOTAL(a.risk IN ('high', 'critical')), TOTAL(a.alive = 'Yes'), TOTAL(a.eol <> ''), TOTAL(a.hasRz) "
            f"FROM {sql}", p).fetchone()
        keys = ['count', 'both', 'rzOnly', 'bcOnly', 'rooms', 'hi', 'alive', 'eol', 'scanned']
        out = {k: int(v) for k, v in zip(keys, r)}
        out['total'] = self.db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
        return out

    def chart_tallies(self):
        """Full value counts behind each chart, over the filtered set."""
        sql, p = self.where()
        sql_rz, p_rz = self.where(extra='a.hasRz = 1')
        bld_cov = {}
        for b, c, n in self.db.execute(f'SELECT a.bld, a.cov, COUNT(*) FROM {sql} GROUP BY a.bld, a.cov', p):
            bld_cov.setdefault(b, {})[c] = n
        ids_rz, p_ids = self.ids(extra='a.hasRz = 1')
        return {
            'cov': dict(self._pairs(f'SELECT a.cov, COUNT(*) FROM {sql} GROUP BY a.cov', p)),
            'bld': dict(self._pairs(f'SELECT a.bld, COUNT(*) FROM {sql} GROUP BY a.bld', p)),
            'bldCov': bld_cov,
            'types': dict(self._pairs(f"SELECT CASE WHEN a.type = '' THEN '(unknown)' ELSE a.type END AS t, COUNT(*) FROM {sql_rz} GROUP BY t", p_rz)),
            'risk': dict(self._pairs(f"SELECT CASE WHEN a.risk = '' THEN '(unknown)' ELSE a.risk END AS t, COUNT(*) FROM {sql_rz} GROUP BY t", p_rz)),
            'months': dict(self._pairs(f"SELECT a.seenM, COUNT(*) FROM {sql} AND a.seenM <> '' GROUP BY a.seenM", p)),
            'ports': dict(self._pairs(f"SELECT value, COUNT(*) FROM asset_facets WHERE facet = 'tcp' AND asset_id IN ({ids_rz}) GROUP BY value", p_ids)),
        }

    def charts(self):
        t = self.chart_tallies()

        def top(fk, counts, limit):
            items = _by_count(counts)
            shown = items[:limit]
            vals = {i[0] for i in shown}
            return shown + [[v, 0] for v in self.state.sel.get(fk, []) if v not in vals]

        blds = top('bld', t['bld'], 12)
        return {
            'cov': sorted(([k, v] for k, v in t['cov'].items()), key=lambda i: _index(COV_ORDER, i[0])),
            'bld': blds,
            'bldTotal': len(t['bld']),
            'bldCov': {b[0]: t['bldCov'].get(b[0], {}) for b in blds},
            'types': top('type', t['types'], 10),
            'risk': sorted(([k, v] for k, v in t['risk'].items()), key=lambda i: (_index(RISK_ORDER, i[0], -1), natural_key(i[0]))),
            'months': sorted([k, v] for k, v in t['months'].items())[-12:],
            'ports': top('tcp', t['ports'], 10),
        }

    def location_tallies(self):
        sql, p = self.where()
        out = {}
        for b, n, both, rz, bc, hi, none in self.db.execute(
                "SELECT a.bld, COUNT(*), TOTAL(a.cov = 'Both'), TOTAL(a.cov = 'runZero only'), TOTAL(a.cov = 'BlueCat only'), "
                f"TOTAL(a.risk IN ('high', 'critical')), TOTAL(a.room = '') FROM {sql} GROUP BY a.bld", p):
            cov = {k: int(v) for k, v in zip(COV_ORDER, (both, rz, bc)) if v}
            out[b] = {'n': n, 'cov': cov, 'hi': int(hi), 'none': int(none), 'rooms': {}}
        for b, room, n in self.db.execute(f"SELECT a.bld, a.roomL, COUNT(*) FROM {sql} AND a.room <> '' GROUP BY a.bld, a.roomL", p):
            out[b]['rooms'][room] = n
        return out

    def locations(self):
        out = []
        for b, v in sorted(self.location_tallies().items(), key=lambda e: (-e[1]['n'], natural_key(e[0]))):
            rooms = sorted(v['rooms'].items(), key=lambda x: (-x[1], natural_key(x[0])))
            out.append({'bld': b, 'n': v['n'], 'cov': v['cov'], 'hi': v['hi'], 'none': v['none'],
                        'roomCount': len(rooms), 'rooms': [list(x) for x in rooms[:28]]})
        return out

    # -- rows ------------------------------------------------------------------------------
    def visible_columns(self):
        cols = [self.columns[k] for k in self.state.cols if k in self.columns]
        return cols or [c for c in BASE_COLUMNS if c.default]

    def rows(self, cols=None, offset=None, limit=None, all_rows=False):
        cols = cols or self.visible_columns()
        fields = ['id'] + sorted({f for c in cols for f in c.fields})
        select = ', '.join(f'a.{column(f)}' for f in fields)
        sql, p = self.where()
        order, op = order_by(self.columns.get(self.state.sort), self.state.dir)
        query = f'SELECT {select} FROM {sql} ORDER BY {order}'
        limit = None if all_rows else (self.state.limit if limit is None else limit)
        offset = self.state.offset if offset is None else offset
        if limit is not None or offset:
            query += ' LIMIT ? OFFSET ?'
            op = op + [-1 if limit is None else limit, offset]
        out = [row_to_record(r) for r in self.db.execute(query, p + op)]
        extra = [c.l for c in cols if c.extra]
        if 'raw' in fields:
            for r in out:
                raw = r.get('raw') or {}
                r['raw'] = {h: raw[h] for h in extra if h in raw}
        return out

    def export_csv(self):
        cols = self.visible_columns()
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator='\n')
        w.writerow([c.l for c in cols])
        for r in self.rows(cols, all_rows=True, offset=0):
            w.writerow([c.text(r) for c in cols])
        return buf.getvalue().rstrip('\n')

    def explore(self):
        kpis = self.kpis()
        out = {'total': kpis['total'], 'count': kpis['count'], 'kpis': kpis, 'facets': self.facets()}
        if self.state.charts:
            out['charts'] = self.charts()
        if self.state.view == 'loc':
            out['locations'] = self.locations()
        else:
            out['rows'] = self.rows()
            out['offset'] = self.state.offset
        return out


def _num(v):
    try:
        return float(v)
    except ValueError:
        return float('inf')


def _index(order, v, missing=99):
    return order.index(v) if v in order else missing


def _by_count(counts):
    return [[k, v] for k, v in sorted(counts.items(), key=lambda i: (-i[1], natural_key(i[0])))]


def detail(db, asset_id):
    row = db.execute('SELECT * FROM assets WHERE id = ?', (asset_id,)).fetchone()
    if not row:
        return None
    r = row_to_record(row)
    nb = []
    if r['room']:
        nb = [dict(x) for x in db.execute('SELECT id, ip, host, type, an, state FROM assets '
                                          'WHERE id <> ? AND bld = ? AND roomL = ? ORDER BY n, id',
                                          (asset_id, r['bld'], r['roomL']))]
    same = db.execute('SELECT COUNT(*) FROM assets WHERE sub = ?', (r['sub'],)).fetchone()[0] if r['sub'] else 0
    return {'asset': r, 'neighbours': nb[:12], 'neighbourCount': len(nb), 'sameSubnet': same}


def meta(db):
    if not has_data(db):
        return {'empty': True, 'facets': [f.public() for f in FACETS], 'columns': [c.public() for c in BASE_COLUMNS],
                'sources': {'rz': None, 'bc': None}, 'stats': {}, 'total': 0, 'rzCount': 0, 'bcCount': 0,
                'rzFields': [{'k': f['k'], 'l': f['l'], 'req': bool(f.get('req'))} for f in RZF]}
    sources = {}
    for kind in ('rz', 'bc'):
        f = effective_file(db, kind)
        has_builtin = db.execute("SELECT 1 FROM source_files WHERE kind = ? AND role = 'builtin'", (kind,)).fetchone()
        sources[kind] = {'name': f['name'], 'builtin': f['role'] == 'builtin', 'rows': f['row_count'],
                         'uploadedAt': f['uploaded_at'],
                         'canReset': bool(has_builtin) and f['role'] == 'active'} if f else None
    stats_row = db.execute("SELECT value FROM meta WHERE key = 'stats'").fetchone()
    total, rz_n = db.execute('SELECT COUNT(*), TOTAL(hasRz) FROM assets').fetchone()
    # BlueCat addresses linked to an asset through another IP share its row, so count the file instead.
    return {'empty': False, 'facets': [f.public() for f in FACETS], 'columns': [c.public() for c in all_columns(db)],
            'sources': sources, 'stats': json.loads(stats_row[0]) if stats_row else {}, 'total': total,
            'rzCount': int(rz_n), 'bcCount': sources['bc']['rows'] if sources['bc'] else 0,
            'rzFields': [{'k': f['k'], 'l': f['l'], 'req': bool(f.get('req'))} for f in RZF]}
