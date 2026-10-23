"""The search box syntax: free text, key:value, -key:value, CIDR / IP ranges and seen:<N>.

Per-asset search strings are built once at import time (asset_search table); a query is compiled
to a SQL condition over that table and the assets table. IP searches look at every address of an
asset (merge.addresses), not only its primary IP.
"""
import re

from .merge import addresses
from .util import cidr_range, ipn


def _join(xs):
    return ' '.join(xs)


def _tags(r):
    return _join(t[0] + ' ' + t[1] for t in r['tags'])


# key -> asset_search column; the column holds what that key searches.
SEARCH_FIELDS = {
    'ip': lambda r: _join(addresses(r)),
    'host': lambda r: r['host'] + ' ' + _join(r['hosts']),
    'mac': lambda r: _join(r['macs']) + ' ' + r['mac'],
    'room': lambda r: r['room'] + ' ' + r['roomL'],
    'bldg': lambda r: r['bld'] + ' ' + r['bldC'],
    'owner': lambda r: r['owner'],
    'type': lambda r: r['type'],
    'os': lambda r: r['os'] + ' ' + r['osv'],
    'hw': lambda r: r['hw'],
    'vendor': lambda r: r['vendor'],
    'risk': lambda r: r['risk'],
    'port': lambda r: _join(r['tcp'] + r['udp']),
    'tag': _tags,
    'sub': lambda r: _join(x['sub'] + ' ' + x['subName'] for x in [r, *r['bcMore']]),
    'state': lambda r: r['state'],
    'cat': lambda r: r['cat'],
    'proto': lambda r: _join(r['proto']),
    'product': lambda r: _join(r['prod']),
    'agent': lambda r: r['agent'],
    'note': lambda r: r['notes'] + ' ' + r['an'],
    'cov': lambda r: r['cov'],
    'alive': lambda r: r['alive'],
    'serial': lambda r: r['serial'],
    'vlan': lambda r: r['vlan'],
    'eol': lambda r: r['eol'],
}
ALIASES = {k: k for k in SEARCH_FIELDS}
ALIASES.update({'name': 'host', 'building': 'bldg', 'model': 'hw', 'ports': 'port', 'tags': 'tag', 'subnet': 'sub',
                'category': 'cat', 'coverage': 'cov'})


def haystack(r):
    """Everything free text searches, including every raw runZero value."""
    parts = [r['ip'], _join(r['ips2']), r['host'], _join(r['hosts']), r['bcHost'], r['an'], _join(r['macs']), r['mac'],
             r['vendor'], r['owner'], r['room'], r['roomL'], r['bld'], r['bldC'], r['sub'], r['subName'], r['state'],
             r['type'], r['cat'], r['os'], r['osv'], r['hw'], r['risk'], _join(r['tcp']), _join(r['udp']),
             _join(r['proto']), _join(r['prod']), _join(' '.join(t) for t in r['tags']), r['agent'], r['notes'],
             r['serial'], r['cov'], _join(r['scopeIps']), r['bcIp'],
             _join(_join([b['ip'], b['host'], b['sub'], b['subName'], b['owner'], b['room'], b['state']]) for b in r['bcMore'])]
    raw = _join(r['raw'].values()) if r['raw'] else ''
    return ' '.join(parts).lower() + ' ' + raw.lower()


def search_row(r):
    """Values for one asset_search row, in SEARCH_FIELDS order after all_text."""
    return [haystack(r)] + [fn(r).lower() for fn in SEARCH_FIELDS.values()]


_TOKEN = re.compile(r'(-?[A-Za-z]+:(?:"[^"]*"|\S*)|"[^"]*"|\S+)')
_NEG_KEY = re.compile(r'-[A-Za-z]+:')
_KEY = re.compile(r'([A-Za-z]+):(.*)')
_QUOTES = re.compile(r'^"|"$')
_SEEN = re.compile(r'([<>]?)(\d+)', re.ASCII)
_CIDR = re.compile(r'\d+\.\d+\.\d+\.\d+/\d+', re.ASCII)
_RANGE = re.compile(r'\d+\.\d+\.\d+\.\d+-\d+\.\d+\.\d+\.\d+', re.ASCII)
_ANY_IP_IN = 'a.id IN (SELECT asset_id FROM asset_ips WHERE n BETWEEN ? AND ?)'


def tokenize(q):
    toks = []
    for m in _TOKEN.finditer(q):
        t, neg, key = m.group(1), False, None
        if t[0] == '-' and len(t) > 1 and _NEG_KEY.match(t):
            neg, t = True, t[1:]
        km = _KEY.fullmatch(t)
        if km and (km.group(1).lower() in ALIASES or km.group(1).lower() == 'seen'):
            key, val = km.group(1).lower(), km.group(2)
        else:
            neg, val = False, m.group(1)
        val = _QUOTES.sub('', val).lower()
        if not val:
            continue
        toks.append((key, val, neg))
    return toks


def compile_search(q):
    """Search text to (sql, params) over aliases a (assets) and s (asset_search), or None."""
    toks = tokenize(q or '')
    if not toks:
        return None
    clauses, params = [], []
    for key, val, neg in toks:
        if key == 'seen':
            mm = _SEEN.fullmatch(val)
            if not mm:
                sql = '0'
            else:
                n = min(int(mm.group(2)), 10**12)
                op = {'>': '>', '<': '<'}.get(mm.group(1), '<=')
                sql = f'(a.seenD >= 0 AND a.seenD {op} ?)'
                params.append(n)
        elif key in (None, 'ip') and _CIDR.fullmatch(val):
            cr = cidr_range(val)
            if cr:
                sql = _ANY_IP_IN
                params += [cr[0], cr[1]]
            else:
                sql = '0'
        elif key in (None, 'ip') and _RANGE.fullmatch(val):
            lo, hi = (ipn(x) for x in val.split('-'))
            if lo >= 0 and hi >= 0:
                sql = _ANY_IP_IN
                params += [lo, hi]
            else:
                sql = '0'
        elif key:
            sql = f'instr(s.f_{ALIASES[key]}, ?) > 0'
            params.append(val)
        else:
            sql = 'instr(s.all_text, ?) > 0'
            params.append(val)
        clauses.append(f'NOT ({sql})' if neg else f'({sql})')
    return ' AND '.join(clauses), params
