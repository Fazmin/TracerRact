"""Table columns: what each needs from a record, how it sorts in SQL and how it reads in a CSV export.

The browser keeps the HTML formatting of each cell; the server owns labels, sort order and export text.
"""
import json
from dataclasses import dataclass
from typing import Callable

from .util import iso_day


@dataclass(frozen=True)
class Column:
    k: str
    l: str
    fields: tuple             # record keys the cell / export text needs
    text: Callable[[dict], str]
    sort: str | None          # SQL expression over alias a; None sorts by IP
    kind: str = 'text'        # 'text' (empty last, natural order) or 'num'
    default: bool = False
    wide: bool = False
    extra: bool = False
    sort_params: tuple = ()

    def public(self):
        return {'k': self.k, 'l': self.l, 'def': self.default, 'w': self.wide, 'extra': self.extra}


def _f(name):
    return lambda r: r[name]


BASE_COLUMNS = [
    Column('n', 'IP address', ('ip',), _f('ip'), 'a.n', 'num', default=True),
    Column('host', 'Hostname', ('host',), _f('host'), 'a.host', default=True, wide=True),
    Column('bld', 'Building', ('bld', 'bldC'), _f('bld'), 'a.bld', default=True),
    Column('roomL', 'Room', ('room',), _f('room'), 'a.roomL', default=True),
    Column('owner', 'Owner', ('owner',), _f('owner'), 'a.owner', default=True, wide=True),
    Column('type', 'Device type', ('type',), _f('type'), 'a.type', default=True),
    Column('hw', 'Model', ('hw', 'os'), lambda r: r['hw'] or r['os'], 'a.hw', default=True, wide=True),
    Column('os', 'Operating system', ('os', 'osv'), lambda r: (r['os'] + ' ' + r['osv']).strip(), 'a.os'),
    Column('risk', 'Risk', ('risk',), _f('risk'), 'a.risk_idx', 'num', default=True),
    Column('last', 'Last seen', ('last',), lambda r: iso_day(r['last']), 'a.last_ms', 'num', default=True),
    Column('cov', 'Coverage', ('cov',), _f('cov'), 'a.cov', default=True),
    Column('state', 'BlueCat state', ('state',), _f('state'), 'a.state', default=True),
    Column('mac', 'MAC', ('mac',), _f('mac'), 'a.mac'),
    Column('vendor', 'MAC vendor', ('vendor',), _f('vendor'), 'a.vendor'),
    Column('sub', 'Subnet', ('sub',), _f('sub'), 'a.sub'),
    Column('subName', 'Subnet name', ('subName',), _f('subName'), 'a.subName', wide=True),
    Column('an', 'BlueCat address name', ('an',), _f('an'), 'a.an', wide=True),
    Column('bcHost', 'BlueCat hostname', ('bcHost',), _f('bcHost'), 'a.bcHost', wide=True),
    Column('cat', 'Category', ('cat',), _f('cat'), 'a.cat'),
    Column('alive', 'Alive', ('alive',), _f('alive'), 'a.alive'),
    Column('first', 'First seen', ('first',), lambda r: iso_day(r['first']), 'a.first_ms', 'num'),
    Column('tcp', 'TCP ports', ('tcp',), lambda r: ' '.join(r['tcp']), 'a.tcp_count', 'num', wide=True),
    # The original compared these arrays numerically, which always tied, so rows fall back to IP order.
    Column('prod', 'Service products', ('prod',), lambda r: ' '.join(r['prod']), None, wide=True),
    Column('tagsT', 'Tags', ('tags',), lambda r: ' '.join(t[0] + '=' + t[1] for t in r['tags']), 'a.tag_count', 'num', wide=True),
    Column('match', 'Matched by', ('match',), _f('match'), 'a.match_by'),
    Column('eol', 'OS end-of-life', ('eol',), _f('eol'), 'a.eol'),
    Column('serial', 'Serial numbers', ('serial',), _f('serial'), 'a.serial'),
    Column('agent', 'Scanned by', ('agent',), _f('agent'), 'a.agent'),
]


def extra_columns(headers, col_map):
    """runZero headers that no explorer field uses, offered as optional columns."""
    used = {headers[c] for c in col_map.values() if 0 <= c < len(headers)}
    out, seen = [], set()
    for h in headers:
        if h in used or h in seen:
            continue
        seen.add(h)
        out.append(Column('x:' + h, h, ('raw',), lambda r, h=h: (r.get('raw') or {}).get(h, ''),
                          'raw_get(a.raw, ?)', wide=True, extra=True, sort_params=(h,)))
    return out


def all_columns(db):
    from .store import effective_file

    rz = effective_file(db, 'rz')
    if not rz:
        return list(BASE_COLUMNS)
    return BASE_COLUMNS + extra_columns(json.loads(rz['headers']), json.loads(rz['column_map']))


def order_by(col, direction):
    """ORDER BY clause and params, with the original's tie-break on IP then load order."""
    d = 'ASC' if direction > 0 else 'DESC'
    tail = 'a.n ASC, a.id ASC'
    if col is None or col.sort is None:
        return tail, []
    if col.kind == 'num':
        return f'{col.sort} {d}, {tail}', list(col.sort_params)
    # Empty values sort after everything ascending (the original used U+FFFF for them).
    return (f"({col.sort} = '') {d}, {col.sort} COLLATE NATURAL {d}, {tail}",
            list(col.sort_params) * 2)
