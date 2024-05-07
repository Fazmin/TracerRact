from conftest import BLUECAT_SAMPLE, RUNZERO_SAMPLE, make_app, upload
from ip_explorer.db import get_db

SAMPLE_STATS = {'rzRows': 8, 'byIp': 1, 'byExtra': 1, 'byMac': 1, 'rzOnly': 5, 'bcOnly': 6, 'noIp': 1, 'badIp': 0,
                'dupIp': 1, 'noLoc': 4}


def _first_rows(path, n):
    return '\n'.join(path.read_text(encoding='utf-8').splitlines()[:n + 1]).encode('utf-8')


def test_starts_empty(client):
    assert client.get('/healthz').get_json() == {'ok': True}
    page = client.get('/')
    assert page.status_code == 200 and b'No data loaded yet' in page.data
    m = client.get('/api/meta').get_json()
    assert m['empty'] is True and m['sources'] == {'rz': None, 'bc': None} and m['total'] == 0
    assert client.get('/api/explore').get_json()['count'] == 0


def test_upload_preview_commit(client):
    bc = upload(client, 'bc', BLUECAT_SAMPLE.read_bytes(), 'bluecat_sample.csv')
    assert bc['count'] == 9  # the row with an invalid IP is dropped
    rz = upload(client, 'rz', RUNZERO_SAMPLE.read_bytes(), 'runzero_sample.csv')
    assert rz['rows'] == 8 and rz['map']['ip'] == rz['headers'].index('address')
    assert rz['ipStats'] == {'rows': 8, 'withIp': 7, 'noIp': 1, 'valid': 7, 'dup': 1}
    assert rz['samples'][rz['headers'].index('hostname')] == 'PRINTER1'

    unmapped = client.put(f"/api/import/{rz['uploadId']}/map", json={'map': {**rz['map'], 'ip': -1}}).get_json()
    assert unmapped['ipStats']['withIp'] == 0
    client.put(f"/api/import/{rz['uploadId']}/map", json={'map': rz['map']})

    pre = client.post('/api/import/preview', json={'rz': rz['uploadId'], 'bc': bc['uploadId']}).get_json()
    assert {k: v for k, v in pre['stats'].items() if k != 'ref'} == SAMPLE_STATS and pre['total'] == 14
    assert client.get('/api/meta').get_json()['empty'] is True  # nothing changes until commit

    m = client.post('/api/import/commit', json={'rz': rz['uploadId'], 'bc': bc['uploadId']}).get_json()
    assert m['empty'] is False and m['total'] == 14 and m['rzCount'] == 8 and m['bcCount'] == 9
    assert m['sources']['rz']['name'] == 'runzero_sample.csv' and m['sources']['rz']['canReset'] is False
    assert [c['k'] for c in m['columns'] if c['extra']] == ['x:id', 'x:site']


def test_bluecat_only(client):
    bc = upload(client, 'bc', BLUECAT_SAMPLE.read_bytes(), 'bluecat_sample.csv')
    m = client.post('/api/import/commit', json={'bc': bc['uploadId']}).get_json()
    assert m['total'] == 9 and m['rzCount'] == 0 and m['sources']['rz'] is None


def test_buildings_rooms_and_search(loaded):
    all_rows = loaded.get('/api/explore?limit=all&cols=n&cols=bld&cols=roomL&cols=cov').get_json()['rows']
    # 10.50.0.3 appears twice on purpose (MAC match, then a duplicate IP); key on the first.
    rows = {}
    for r in all_rows:
        rows.setdefault(r['ip'], r)
    assert rows['130.113.10.20']['bldC'] == 'ITB' and rows['130.113.10.20']['cov'] == 'Both'
    assert rows['10.50.0.2']['bldC'] == 'JHE'                # matched on the extra IP 130.113.20.5
    assert rows['10.50.0.3']['bldC'] == 'JHE' and rows['10.50.0.3']['cov'] == 'Both'  # unique MAC
    assert [r['cov'] for r in all_rows if r['ip'] == '10.50.0.3'] == ['Both', 'runZero only']
    assert rows['10.50.0.4']['cov'] == 'runZero only'        # MAC shared by two BlueCat rows
    assert rows['10.60.0.5']['bldC'] == 'BSB'                # runZero location tag
    assert rows['130.113.20.99']['bldC'] == 'JHE'            # inside a BlueCat subnet
    assert rows['172.16.5.10']['bldC'] == 'VPN'

    rooms = loaded.get('/api/facets/room').get_json()['items']
    assert ['ITB · 242', 2] in rooms and ['JHE · 302', 2] in rooms

    def count(q):
        return loaded.get('/api/explore', query_string={'q': q}).get_json()['count']
    assert count('type:printer') == 1
    assert count('port:3389') == 2
    assert count('seen:>90') == 1
    assert count('130.113.10.0/24') == 5
    assert count('-cov:bluecat') == 8
    assert count('facilities') == 1

    k = loaded.get('/api/explore?f.cov=Both&f.cov=runZero+only').get_json()
    assert k['count'] == 8 and k['kpis']['rooms'] == 2
    assert k['facets']['cov']['items'] == [['Both', 3], ['runZero only', 5], ['BlueCat only', 6]]


def test_detail_and_export(loaded):
    ids = {r['ip']: r['id'] for r in loaded.get('/api/explore?limit=all&cols=n').get_json()['rows']}
    d = loaded.get(f"/api/assets/{ids['130.113.10.22']}").get_json()
    assert d['asset']['roomL'] == 'ITB · 242' and d['neighbourCount'] == 1 and d['sameSubnet'] == 5
    rz = loaded.get(f"/api/assets/{ids['130.113.10.20']}").get_json()['asset']
    assert list(rz['raw'])[:3] == ['id', 'site', 'detected_by'] and rz['match'] == 'IP'
    assert loaded.get('/api/assets/999999').status_code == 404

    r = loaded.get('/api/export.csv?q=type:printer&cols=n&cols=host&cols=x:site&download=1')
    assert 'attachment' in r.headers['Content-Disposition']
    assert r.get_data(as_text=True) == '"IP address","Hostname","site"\n"130.113.10.20","PRINTER1","Primary"'


def test_new_upload_replaces_previous(loaded):
    rz = upload(loaded, 'rz', _first_rows(RUNZERO_SAMPLE, 4), 'runzero_newer.csv')
    m = loaded.post('/api/import/commit', json={'rz': rz['uploadId']}).get_json()
    assert m['rzCount'] == 4 and m['bcCount'] == 9
    with loaded.application.app_context():
        files = [tuple(r) for r in get_db().execute('SELECT kind, name, role FROM source_files ORDER BY id')]
    assert files == [('bc', 'bluecat_sample.csv', 'active'), ('rz', 'runzero_newer.csv', 'active')]
    # A committed upload cannot be committed again.
    assert loaded.post('/api/import/commit', json={'rz': rz['uploadId']}).status_code == 400


def test_upload_errors(client):
    assert 'IP Address' in upload(client, 'bc', b'Host,Room\na,1\n', 'nope.csv')['error']
    assert upload(client, 'rz', b'address\n', 'empty.csv')['error'] == 'This file has no data rows.'
    assert client.post('/api/import/upload', data={'kind': 'xx'}).status_code == 400


def test_init_db_without_files_starts_empty(tmp_path):
    app = make_app(tmp_path / 'db.sqlite')
    out = app.test_cli_runner().invoke(args=['init-db', '--if-empty'])
    assert out.exit_code == 0 and 'empty database' in out.output
    missing = app.test_cli_runner().invoke(args=['init-db', '--bluecat', str(tmp_path / 'nope.csv')])
    assert missing.exit_code != 0 and 'File not found' in missing.output


def test_init_db_builtin_and_reset(tmp_path):
    app = make_app(tmp_path / 'db.sqlite')
    runner = app.test_cli_runner()
    out = runner.invoke(args=['init-db', '--bluecat', str(BLUECAT_SAMPLE), '--runzero', str(RUNZERO_SAMPLE)])
    assert 'Loaded 9 BlueCat addresses and 8 runZero assets (14 merged records)' in out.output
    assert 'keeping it' in runner.invoke(args=['init-db', '--if-empty']).output

    client = app.test_client()
    rz = upload(client, 'rz', _first_rows(RUNZERO_SAMPLE, 3), 'runzero_small.csv')
    m = client.post('/api/import/commit', json={'rz': rz['uploadId']}).get_json()
    assert m['rzCount'] == 3 and m['sources']['rz']['canReset'] is True
    back = client.post('/api/import/reset/rz').get_json()
    assert back['rzCount'] == 8 and back['sources']['rz']['builtin'] is True
