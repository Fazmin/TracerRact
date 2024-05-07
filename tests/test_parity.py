"""Parity with the original single-file explorer, checked on your real exports.

Opt-in, because it needs your data and that stays outside the project:

    IPX_BLUECAT_CSV=/path/to/bluecat.csv IPX_RUNZERO_CSV=/path/to/runzero.csv .venv/bin/pytest tests/test_parity.py

It runs the original HTML's JavaScript in Node (tests/parity/js_reference.mjs) to produce reference
results, then checks that the Python merge and server-side queries give the same answers. Everything
derived from the data is written to a temporary folder that is deleted afterwards.
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
from ip_explorer.db import get_db, init_schema
from ip_explorer.facets import FACETS
from ip_explorer.merge import merge
from ip_explorer.query import Query, State
from ip_explorer.store import row_to_record

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
    yield conn
    ctx.pop()


def _inputs():
    bc = ingest.bluecat_from_table(ingest.parse_csv(ingest.decode(Path(BLUECAT).read_bytes())))['objs']
    rz = ingest.parse_csv(ingest.decode(Path(RUNZERO).read_bytes()))
    return bc, rz, ingest.auto_map(rz['headers'])


def _strip(rec):
    return {k: v for k, v in rec.items() if k != 'fv'}


def test_auto_map(golden):
    _, rz, m = _inputs()
    assert rz['headers'] == golden['rzHeaders'] and m == golden['rzMap']


def test_records_stats_and_facet_values(golden):
    recs, stats = merge(*_inputs())
    assert stats == golden['stats']
    assert len(recs) == len(golden['records'])
    for mine, ref in zip(recs, golden['records']):
        assert mine == _strip(ref), f"record {ref['id']} differs"
        assert {f.k: f.values(mine) for f in FACETS} == ref['fv'], f"facets of record {ref['id']} differ"


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
    assert stats == syn['stats']
    assert [r for r in recs if r['hasRz']] == syn['records']


def test_database_round_trip(golden, db):
    rows = db.execute('SELECT * FROM assets ORDER BY id').fetchall()
    assert [row_to_record(r) for r in rows] == [_strip(r) for r in golden['records']]


def test_queries(golden, db):
    """Same rows in the same order, facet counts, KPIs, chart tallies and location groups."""
    ip_col = [c for c in BASE_COLUMNS if c.k == 'n']
    failures = []
    for case, ref in zip(golden['cases'], golden['results']):
        sort = case.get('sort') or {'k': 'n', 'd': 1}
        q = Query(db, State(q=case.get('q', ''), sel=case.get('sel') or {}, sort=sort['k'], dir=sort['d']))
        locs = {b: {k: v for k, v in g.items() if k != 'code'} for b, g in ref['locations'].items()}
        checks = {
            'ids': ([r['id'] for r in q.rows(ip_col, all_rows=True, offset=0)], ref['ids']),
            'counts': (q.facet_counts(), ref['counts']),
            'kpis': (q.kpis(), ref['kpis']),
            'charts': (q.chart_tallies(), ref['charts']),
            'locations': (q.location_tallies(), locs),
        }
        failures += [f'{case}: {name} differs' for name, (mine, theirs) in checks.items() if mine != theirs]
    assert not failures, '\n'.join(failures[:30])
