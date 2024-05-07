"""Facet definitions (the left-hand filter rail). Values are precomputed per asset at build time."""
from dataclasses import dataclass, field
from typing import Callable

RISK_ORDER = ['critical', 'high', 'medium', 'low', 'info', 'none']
COV_ORDER = ['Both', 'runZero only', 'BlueCat only']
SEEN_ORDER = ['Within 7 days', '8 to 30 days', '31 to 90 days', 'Over 90 days', '(unknown)', '(not scanned)']


@dataclass(frozen=True)
class Facet:
    k: str
    l: str
    get: Callable[[dict], list]
    open: bool = False
    rz: bool = False          # runZero-only field: assets that were not scanned get '(not scanned)'
    search: bool = False      # show a find box above the list
    num: bool = False         # numeric values (ports)
    order: list = field(default_factory=list)

    def values(self, r):
        return ['(not scanned)'] if self.rz and not r['hasRz'] else self.get(r)

    def public(self):
        return {'k': self.k, 'l': self.l, 'open': self.open, 'search': self.search}


FACETS = [
    Facet('cov', 'Coverage', lambda r: [r['cov']], open=True, order=COV_ORDER),
    Facet('bld', 'Building', lambda r: [r['bld']], open=True, search=True),
    Facet('room', 'Room', lambda r: [r['roomL']] if r['room'] else ['(no room)'], open=True, search=True),
    Facet('type', 'Device type', lambda r: [r['type'] or '(unknown)'], open=True, rz=True, search=True),
    Facet('risk', 'Risk', lambda r: [r['risk'] or '(unknown)'], open=True, rz=True, order=RISK_ORDER),
    Facet('cat', 'Category', lambda r: [r['cat'] or '(unknown)'], rz=True),
    Facet('alive', 'Alive when last scanned', lambda r: [r['alive'] or '(unknown)'], rz=True),
    Facet('seen', 'Last seen', lambda r: [r['seenB']], rz=True, order=SEEN_ORDER),
    Facet('seenM', 'Last seen month', lambda r: [r['seenM']] if r['seenM'] else [], rz=True, search=True),
    Facet('sub', 'Subnet', lambda r: [r['sub'] + '  ' + r['subName']] if r['sub'] else ['(no subnet)'], search=True),
    Facet('blk', 'Address block', lambda r: [r['blk']]),
    Facet('owner', 'Owner', lambda r: [r['owner']] if r['owner'] else ['(no owner)'], search=True),
    Facet('state', 'BlueCat state', lambda r: [(r['state'] or '(blank)') if r['hasBc'] else '(not in BlueCat)']),
    Facet('os', 'Operating system', lambda r: [r['os'] or '(unknown)'], rz=True, search=True),
    Facet('hw', 'Hardware / model', lambda r: [r['hw'] or '(unknown)'], rz=True, search=True),
    Facet('vendor', 'MAC vendor', lambda r: [r['vendor'] or '(unknown)'], rz=True, search=True),
    Facet('tcp', 'Open TCP port', lambda r: list(r['tcp']), rz=True, search=True, num=True),
    Facet('proto', 'Service protocol', lambda r: list(r['proto']), rz=True, search=True),
    Facet('prod', 'Service product', lambda r: list(r['prod']), rz=True, search=True),
    Facet('tag', 'runZero tag', lambda r: [t[0] + ': ' + t[1] for t in r['tags']], rz=True, search=True),
    Facet('eol', 'OS end-of-life', lambda r: ['End-of-life OS' if r['eol'] else 'Not flagged'], rz=True),
    Facet('agent', 'Scanned by', lambda r: [r['agent'] or '(unknown)'], rz=True),
    Facet('det', 'Detected by', lambda r: [r['det'] or '(unknown)'], rz=True),
    Facet('match', 'Matched by', lambda r: [v for v in [r['match'] or ('' if r['hasRz'] and r['hasBc'] else '(not matched)')] if v]),
]
FK = {f.k: f for f in FACETS}
