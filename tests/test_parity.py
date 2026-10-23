"""Parity with the original single-file explorer, checked on your real exports.

Opt-in, because it needs your data and that stays outside the project:

    IPX_BLUECAT_CSV=/path/to/bluecat.csv IPX_RUNZERO_CSV=/path/to/runzero.csv .venv/bin/pytest tests/test_parity.py

It runs the original HTML's JavaScript in Node (tests/parity/js_reference.mjs) to produce reference
results, then checks that the Python merge and server-side queries give the same answers. Everything
derived from the data is written to a temporary folder that is deleted afterwards.

The explorer now does more with an asset's other addresses than the original did, so the comparison
leaves addresses_scope unmapped (the original never read it), ignores the record fields and stats
added since, and only checks that IP range searches find at least what the original found. An asset
with no runZero address is now shown by a stand-in IP (merge._stand_in_ip), which moves it in IP
order and out of the 'No IP' address block: such assets are compared as a set, and the address
block counts and filters are skipped. Results still differ, by design, where an asset has two
addresses in BlueCat (the second is linked to it instead of staying BlueCat only) or only a
secondary address inside a BlueCat subnet.
"""
import gzip
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import ROOT, make_app
from ip_explorer import ingest, store
from ip_explorer.columns import BASE_COLUMNS
from ip_explorer.db import get_db, init_schema, transaction
from ip_explorer.facets import FACETS
from ip_explorer.merge import merge
from ip_explorer.query import Query, State
from ip_explorer.search import tokenize, _CIDR, _RANGE
from ip_explorer.store import row_to_record

NEW_FIELDS = ('scopeIps', 'bcIp', 'bcMore', 'ipSrc')
NEW_STATS = ('byScope', 'bcLinked')

BLUECAT = os.environ.get('IPX_BLUECAT_CSV')
RUNZERO = os.environ.get('IPX_RUNZERO_CSV')
HTML = os.environ.get('IPX_ORIGINAL_HTML') or str(ROOT / 'IP Asset Explorer_McMaster_2026.html')


def _skip_reason():
    if not (BLUECAT and RUNZERO):
        return 'set IPX_BLUECAT_CSV and IPX_RUNZERO_CSV to compare with the original on real exports'
    for p in (BLUECAT, RUNZERO, HTML):
        if not Path(p).is_file():
            return f'not found: {p}'
    if not shutil.which('node'):
        return 'node is not installed'
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or '')


@pytest.fixture(scope='module')
def workdir():
    with tempfile.TemporaryDirectory(prefix='ipx-parity-') as d:
        yield Path(d)


@pytest.fixture(scope='module')
def golden(workdir):
    out = workdir / 'golden.json.gz'
    subprocess.run(['node', str(ROOT / 'tests' / 'parity' / 'js_reference.mjs'), '--html', HTML,
                    '--bluecat', BLUECAT, '--runzero', RUNZERO, '--out', str(out)], check=True, capture_output=True)
    with gzip.open(out, 'rt', encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='module')
def db(workdir):
    app = make_app(workdir / 'explorer.sqlite')
    ctx = app.app_context()
    ctx.push()
    conn = get_db()
    init_schema(conn, reset=True)
    store.seed(conn, BLUECAT, RUNZERO)
    with transaction(conn):
        frow = store.effective_file(conn, 'rz')
        conn.execute('UPDATE source_files SET column_map = ? WHERE id = ?',
                     (json.dumps({**json.loads(frow['column_map']), 'scopeIps': -1}), frow['id']))
        store.rebuild(conn)
    yield conn
    ctx.pop()


def _inputs():
    bc = ingest.bluecat_from_table(ingest.parse_csv(ingest.decode(Path(BLUECAT).read_bytes())))['objs']
    rz = ingest.parse_csv(ingest.decode(Path(RUNZERO).read_bytes()))
    return bc, rz, {**ingest.auto_map(rz['headers']), 'scopeIps': -1}


def _strip(rec):
    return {k: v for k, v in rec.items() if k != 'fv'}


def _old(d, new):
    """Only what the original had."""
    return {k: v for k, v in d.items() if k not in new}


def _no_stand_in(rec):
    """The original showed no IP for an asset without a runZero address."""
    return {**rec, 'ip': '', 'n': -1, 'blk': 'No IP'} if rec['ipSrc'] else rec


def _as_original(rec):
    return _old(_no_stand_in(rec), NEW_FIELDS)


def test_auto_map(golden):
    _, rz, m = _inputs()
    assert rz['headers'] == golden['rzHeaders'] and _old(m, ['scopeIps']) == golden['rzMap']


def test_records_stats_and_facet_values(golden):
    recs, stats = merge(*_inputs())
    assert _old(stats, NEW_STATS) == golden['stats']
    assert len(recs) == len(golden['records'])
    for mine, ref in zip(recs, golden['records']):
        assert _as_original(mine) == _strip(ref), f"record {ref['id']} differs"
        assert {f.k: f.values(_no_stand_in(mine)) for f in FACETS} == ref['fv'], f"facets of record {ref['id']} differ"


def test_synthetic_match_paths(golden):
    """Extra-IP and MAC matches, ambiguous MAC, duplicate / invalid / missing IPs."""
    syn = golden['synthetic']
    if not syn:
        pytest.skip('the exports are too small to build the synthetic case')
    bc, rz, m = _inputs()
    rows = [list(r) for r in rz['rows']]
    for row, header, value in syn['edits']:
        rows[row][rz['headers'].index(header)] = value
    recs, stats = merge(bc, {'headers': rz['headers'], 'rows': rows}, m)
    assert _old(stats, NEW_STATS) == syn['stats']
    assert [_as_original(r) for r in recs if r['hasRz']] == syn['records']


def test_database_round_trip(golden, db):
    rows = db.execute('SELECT * FROM assets ORDER BY id').fetchall()
    assert [_as_original(row_to_record(r)) for r in rows] == [_strip(r) for r in golden['records']]


def _ip_range(q):
    return any(key in (None, 'ip') and (_CIDR.fullmatch(val) or _RANGE.fullmatch(val)) for key, val, _ in tokenize(q or ''))


def test_queries(golden, db):
    """Same rows in the same order, facet counts, KPIs, chart tallies and location groups."""
    ip_col = [c for c in BASE_COLUMNS if c.k == 'n']
    stand_ins = {i for (i,) in db.execute("SELECT id FROM assets WHERE ipSrc <> ''")}

    def others(ids):
        return [i for i in ids if i not in stand_ins]

    failures = []
    for case, ref in zip(golden['cases'], golden['results']):
        sort = case.get('sort') or {'k': 'n', 'd': 1}
        q = Query(db, State(q=case.get('q', ''), sel=case.get('sel') or {}, sort=sort['k'], dir=sort['d']))
        ids = [r['id'] for r in q.rows(ip_col, all_rows=True, offset=0)]
        if _ip_range(case.get('q')):
            # These now also match an asset's scope and extra IPs, so they find more than the original.
            if not set(ref['ids']) <= set(ids):
                failures.append(f'{case}: misses rows the original found')
            continue
        # Stand-in IPs move their assets in IP order: the other rows keep the original's order.
        checks = {'ids': (others(ids), others(ref['ids']))}
        if 'blk' not in (case.get('sel') or {}):
            locs = {b: {k: v for k, v in g.items() if k != 'code'} for b, g in ref['locations'].items()}
            checks.update({
                'stand-in ids': (set(ids) & stand_ins, set(ref['ids']) & stand_ins),
                'counts': (_old(q.facet_counts(), ['blk']), _old(ref['counts'], ['blk'])),
                'kpis': (q.kpis(), ref['kpis']),
                'charts': (q.chart_tallies(), ref['charts']),
                'locations': (q.location_tallies(), locs),
            })
        failures += [f'{case}: {name} differs' for name, (mine, theirs) in checks.items() if mine != theirs]
    assert not failures, '\n'.join(failures[:30])
