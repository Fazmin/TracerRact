import sqlite3
import subprocess

import pytest

from conftest import make_app
from ip_explorer import probe, scheduler, tracker
from ip_explorer.db import get_db

DAY = tracker.DAY
T0 = 1_700_000_000


def fake_check(up=()):
    """A probe.check stand-in: the addresses in `up` answer ICMP, the rest do not."""
    calls = []

    def check(ips):
        calls.append(list(ips))
        return {ip: (True, 1.5, 'icmp') if ip in up else (False, None, '') for ip in ips}
    check.calls = calls
    return check


def ids_by_ip(db):
    return {r['ip']: r['id'] for r in db.execute('SELECT id, ip FROM track_targets')}


def status(db, ip, now):
    return tracker.for_ip(db, ip, now=now)['status']


def test_parse_targets():
    nums, bad = tracker.parse_targets('10.0.0.1, 10.0.0.1;10.0.0.8/30\n10.0.0.20-10.0.0.22 10.0.0.30/32 nope 10.0.0.300 10.0.0.9-10.0.0.5')
    assert [tracker.ip_str(n) for n in nums] == ['10.0.0.1', '10.0.0.9', '10.0.0.10', '10.0.0.20', '10.0.0.21', '10.0.0.22',
                                                 '10.0.0.30']
    assert bad == ['nope', '10.0.0.300', '10.0.0.9-10.0.0.5']
    assert len(tracker.parse_targets('10.0.0.0/31')[0]) == 2
    with pytest.raises(ValueError, match='at most 10,000'):
        tracker.parse_targets('10.0.0.0/16')
    with pytest.raises(ValueError, match='at most'):
        tracker.parse_targets('10.0.0.0/19 10.1.0.0/19')


def test_add_and_list(client):
    r = client.post('/api/tracker/targets', json={'ips': '130.113.10.20 130.113.10.21 bogus', 'every': '4h', 'label': 'lab'})
    assert r.get_json() == {'added': 2, 'existing': 0, 'invalid': ['bogus']}
    again = client.post('/api/tracker/targets', json={'ips': '130.113.10.20', 'every': '1d'}).get_json()
    assert again['added'] == 0 and again['existing'] == 1
    assert client.post('/api/tracker/targets', json={'ips': 'bogus'}).status_code == 400
    assert 'how often' in client.post('/api/tracker/targets', json={'ips': '10.0.0.1', 'every': '5m'}).get_json()['error']

    rows = client.get('/api/tracker/targets').get_json()['rows']
    assert [(r['ip'], r['status'], r['everyLabel'], r['label'], r['nextAt']) for r in rows] == [
        ('130.113.10.20', 'pending', 'Every 4 hours', 'lab', 0), ('130.113.10.21', 'pending', 'Every 4 hours', 'lab', 0)]
    s = client.get('/api/tracker').get_json()
    assert s['counts'] == {'up': 0, 'down': 0, 'pending': 2, 'paused': 0, 's30': 0, 's60': 0, 's90': 0, 's180': 0, 's365': 0,
                           'all': 2}
    assert [i['k'] for i in s['intervals']] == ['4h', '8h', '12h', '1d', '3d', '1mo', '2mo']
    assert [(b['k'], b['l']) for b in s['silent']] == [('s30', '30+ days'), ('s60', '60+ days'), ('s90', '90+ days'),
                                                      ('s180', '6+ months'), ('s365', '12+ months')]
    assert s['scans'] == [{'id': None, 'name': 'Added one at a time', 'counts': s['counts'], 'due': 2, 'nextAt': 0, 'lastAt': None}]


def test_run_due_records_results(app):
    with app.app_context():
        db = get_db()
        tracker.add(db, tracker.parse_targets('10.0.0.1 10.0.0.2 10.0.0.3')[0], '4h', now=T0)
        ids = ids_by_ip(db)
        tracker.update(db, [ids['10.0.0.3']], paused=True)
        check = fake_check(up={'10.0.0.1'})

        assert tracker.run_due(db, check, clock=lambda: T0) == {'checked': 2, 'up': 1}
        assert check.calls == [['10.0.0.1', '10.0.0.2']]  # paused targets are skipped
        t = db.execute("SELECT * FROM track_targets WHERE ip = '10.0.0.1'").fetchone()
        assert (t['last_at'], t['last_up'], t['last_up_at'], t['checks'], t['ups'], t['next_at'], t['last_via']) == \
            (T0, 1, T0, 1, 1, T0 + 4 * 3600, 'icmp')
        assert [status(db, ip, T0) for ip in ('10.0.0.1', '10.0.0.2', '10.0.0.3')] == ['up', 'down', 'paused']

        # Nothing is due until the interval has passed.
        assert tracker.run_due(db, check, clock=lambda: T0 + 3600)['checked'] == 0
        assert tracker.run_due(db, check, clock=lambda: T0 + 4 * 3600)['checked'] == 2
        assert db.execute('SELECT COUNT(*) FROM track_checks').fetchone()[0] == 4

        # A longer interval moves the next check; a shorter one that has already passed makes it due now.
        tracker.update(db, [ids['10.0.0.1']], every='1d')
        assert db.execute('SELECT next_at FROM track_targets WHERE id = ?', (ids['10.0.0.1'],)).fetchone()[0] == T0 + 4 * 3600 + DAY


def test_silence_buckets(app):
    with app.app_context():
        db = get_db()
        tracker.add(db, tracker.parse_targets('10.0.0.1 10.0.0.2')[0], '2mo', now=T0)
        tracker.run_due(db, fake_check(), clock=lambda: T0)
        assert tracker.run_due(db, fake_check(up={'10.0.0.2'}), clock=lambda: T0 + 60 * DAY)['checked'] == 2

        def silent(ip, days):
            return tracker.for_ip(db, ip, now=T0 + days * DAY)['silent']
        # .1 never answered: its silence counts from when tracking started.
        assert [silent('10.0.0.1', d) for d in (29, 31, 61, 91, 181, 366)] == [0, 30, 60, 90, 180, 365]
        assert status(db, '10.0.0.1', T0 + 366 * DAY) == 'down'
        # .2 answered at day 60, then stopped: counted from that reply.
        assert silent('10.0.0.2', 60) == 0
        tracker.run_due(db, fake_check(), clock=lambda: T0 + 120 * DAY)
        assert silent('10.0.0.2', 120) == 60 and silent('10.0.0.2', 151) == 90

        # At day 200: .1 has had no reply for 200 days, .2 for 140. The buckets add up.
        now = T0 + 200 * DAY
        assert tracker.counts(db, now=now) == {'up': 0, 'down': 2, 'pending': 0, 'paused': 0, 's30': 2, 's60': 2, 's90': 2,
                                               's180': 1, 's365': 0, 'all': 2}
        assert [r['ip'] for r in tracker.targets(db, bucket='s180', now=now)['rows']] == ['10.0.0.1']
        assert [r['ip'] for r in tracker.targets(db, sort='status', now=now)['rows']] == ['10.0.0.1', '10.0.0.2']
        p = tracker.profile(db, ids_by_ip(db)['10.0.0.2'], now=T0 + 120 * DAY)
        assert p['target']['silentSince'] == T0 + 60 * DAY and [c[1] for c in p['checks']] == [0, 1, 0]
        assert p['target']['nextSilent'] == {'d': 90, 'l': '90+ days', 'at': T0 + 150 * DAY}
        csv = tracker.export_csv(db, bucket='s90', now=now).splitlines()
        assert csv[0].startswith('"IP address","Status","Days without a reply","Last reply (UTC)"')
        assert [line.split(',')[:3] for line in csv[1:]] == [['"10.0.0.1"', '"No reply 6+ months"', '"200"'],
                                                             ['"10.0.0.2"', '"No reply 90+ days"', '"140"']]


def test_list_filters_and_bulk_changes(client):
    client.post('/api/tracker/targets', json={'ips': '10.0.0.1-10.0.0.3 10.1.0.1', 'every': '1d', 'label': 'printers'})
    with client.application.app_context():
        db = get_db()
        tracker.run_due(db, fake_check(up={'10.0.0.1', '10.1.0.1'}))
        ids = ids_by_ip(db)

    def ips(**args):
        return [r['ip'] for r in client.get('/api/tracker/targets', query_string=args).get_json()['rows']]
    assert ips(bucket='up') == ['10.0.0.1', '10.1.0.1'] and ips(bucket='down') == ['10.0.0.2', '10.0.0.3']
    assert ips(q='10.0.0.0/24') == ['10.0.0.1', '10.0.0.2', '10.0.0.3'] and ips(q='printers 10.1') == ['10.1.0.1']
    assert ips(sort='status')[:2] == ['10.0.0.2', '10.0.0.3'] and ips(sort='ip', dir='-1')[0] == '10.1.0.1'
    assert client.get('/api/tracker/targets').get_json()['rows'][0]['recent'] == '1'

    sel = [ids['10.0.0.2'], ids['10.0.0.3']]
    assert client.patch('/api/tracker/targets', json={'ids': sel, 'paused': True}).get_json() == {'updated': 2}
    assert ips(bucket='paused') == ['10.0.0.2', '10.0.0.3']
    assert client.patch('/api/tracker/targets', json={'ids': sel, 'every': 'nope'}).status_code == 400
    assert client.delete('/api/tracker/targets', json={'ids': sel}).get_json() == {'removed': 2}
    with client.application.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM track_checks').fetchone()[0] == 2  # their history went too
    assert client.get(f'/api/tracker/targets/{sel[0]}').status_code == 404

    r = client.get('/api/tracker/export.csv?bucket=up&download=1')
    assert 'attachment' in r.headers['Content-Disposition'] and len(r.get_data(as_text=True).splitlines()) == 3


def test_check_now(client, monkeypatch):
    monkeypatch.setattr(probe, 'check', lambda ips, tcp_fallback=True: {ip: (True, 2.0, 'tcp/443') for ip in ips})
    client.post('/api/tracker/targets', json={'ips': '10.0.0.1 10.0.0.2'})
    with client.application.app_context():
        ids = list(ids_by_ip(get_db()).values())
    assert client.post('/api/tracker/check', json={'ids': ids}).get_json() == {'checked': 2, 'up': 2}
    p = client.get(f'/api/tracker/targets/{ids[0]}').get_json()
    assert p['target']['status'] == 'up' and p['target']['lastVia'] == 'tcp/443' and len(p['checks']) == 1

    # Larger requests are queued for the scheduler.
    client.post('/api/tracker/targets', json={'ips': '10.2.0.0/27'})
    with client.application.app_context():
        many = list(ids_by_ip(get_db()).values())
    assert client.post('/api/tracker/check', json={'ids': many}).get_json() == {'queued': len(many), 'paced': 0}


def test_track_from_explorer(loaded):
    itb = {'q': 'cov:"bluecat only"', 'f.bld': 'ITB · Computing & Software'}
    r = loaded.post('/api/tracker/from-explorer', query_string=itb, json={'every': '3d'}).get_json()
    assert r['added'] == 3 and r['joined'] == r['existing'] == 0
    loaded.post('/api/tracker/from-explorer?q=cov:"bluecat only"', json={'every': '3d', 'name': 'BlueCat only', 'paced': True})
    rows = loaded.get('/api/tracker/targets').get_json()['rows']
    assert [r['ip'] for r in rows] == ['130.113.10.1', '130.113.10.22', '130.113.10.23', '172.16.5.10']
    assert rows[1]['room'] == 'ITB · 242' and rows[1]['cov'] == 'BlueCat only' and rows[0]['everyLabel'] == 'Every 3 days'
    itb = 'Explorer: cov:"bluecat only"; Building: ITB · Computing & Software'
    assert [(r['scan'], r['paced']) for r in rows] == [(itb, False)] * 3 + [('BlueCat only', True)]

    asset_ids = {r['ip']: r['id'] for r in loaded.get('/api/explore?limit=all&cols=n').get_json()['rows']}
    assert loaded.get(f"/api/assets/{asset_ids['130.113.10.22']}").get_json()['tracking']['status'] == 'pending'
    assert loaded.get(f"/api/assets/{asset_ids['130.113.10.20']}").get_json()['tracking'] is None
    assert loaded.post('/api/tracker/from-explorer?q=nothing-matches-this', json={}).status_code == 400


def test_scans(client):
    client.post('/api/tracker/targets', json={'ips': '10.0.0.9 10.0.1.1'})  # on their own
    a = client.post('/api/tracker/scans', json={'ips': '10.0.1.0/30, 10.0.0.9 bogus', 'every': '4h', 'paced': True}).get_json()
    # The /30's two hosts and 10.0.0.9; the two tracked on their own join the scan.
    assert {k: a[k] for k in ('added', 'joined', 'existing', 'invalid')} == {'added': 1, 'joined': 2, 'existing': 0, 'invalid': ['bogus']}
    b = client.post('/api/tracker/scans', json={'ips': '10.0.1.2-10.0.1.3', 'name': 'B'}).get_json()
    assert (b['added'], b['joined'], b['existing']) == (1, 0, 1)  # 10.0.1.2 stays in the first scan
    r = client.post('/api/tracker/scans', json={'ips': '10.0.1.2'})
    assert r.status_code == 400 and 'other scans' in r.get_json()['error']

    def rows(**args):
        return client.get('/api/tracker/targets', query_string=args).get_json()['rows']
    scans = client.get('/api/tracker').get_json()['scans']
    assert [(s['id'], s['name'], s['spec'], s['everyLabel'], s['paced'], s['counts']['all']) for s in scans] == [
        (a['scan'], '10.0.1.0/30 and 1 more', '10.0.1.0/30 10.0.0.9', 'Every 4 hours', True, 3),
        (b['scan'], 'B', '10.0.1.2-10.0.1.3', 'Every day', False, 1)]
    assert [(r['ip'], r['every'], r['paced']) for r in rows(scan=a['scan'])] == [
        ('10.0.0.9', 4 * 3600, True), ('10.0.1.1', 4 * 3600, True), ('10.0.1.2', 4 * 3600, True)]
    assert rows(scan='none') == [] and [r['ip'] for r in rows(q='b')] == ['10.0.1.3']  # the search matches scan names
    assert client.get('/api/tracker/targets?scan=x').status_code == 400
    t = client.get('/api/tracker/targets', query_string={'scan': a['scan']}).get_json()
    assert t['counts']['all'] == 3 and t['counts']['pending'] == 3

    # Its settings apply to every address in it.
    r = client.patch(f"/api/tracker/scans/{a['scan']}", json={'every': '1d', 'paced': False, 'paused': True})
    assert r.get_json() == {'updated': 3}
    assert {(r['every'], r['paced'], r['paused']) for r in rows(scan=a['scan'])} == {(DAY, False, True)}
    assert client.patch(f"/api/tracker/scans/{a['scan']}", json={'name': 'Lab subnet'}).get_json() == {'updated': 0}
    assert client.get('/api/tracker').get_json()['scans'][1]['name'] == 'Lab subnet'
    assert client.patch(f"/api/tracker/scans/{a['scan']}", json={'name': ' '}).status_code == 400
    assert client.patch('/api/tracker/scans/999', json={'paused': False}).status_code == 404

    # A scan goes when its last address does; removing a scan removes its addresses and history.
    client.delete('/api/tracker/targets', json={'ids': [rows(scan=b['scan'])[0]['id']]})
    assert [s['name'] for s in client.get('/api/tracker').get_json()['scans']] == ['Lab subnet']
    with client.application.app_context():
        tracker.check_now(get_db(), [r['id'] for r in rows()], fake_check())
    assert client.delete(f"/api/tracker/scans/{a['scan']}").get_json() == {'removed': 3}
    assert rows() == [] and client.get('/api/tracker').get_json()['scans'] == []
    with client.application.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM track_checks').fetchone()[0] == 0
    assert client.delete(f"/api/tracker/scans/{a['scan']}").status_code == 404


def test_paced_checks_go_one_at_a_time(app):
    with app.app_context():
        db = get_db()
        tracker.add_scan(db, tracker.parse_targets('10.0.0.1-10.0.0.3')[0], '10.0.0.1-10.0.0.3', every='4h', paced=True, now=T0)
        tracker.add(db, tracker.parse_targets('10.0.1.1')[0], '4h', now=T0)
        check = fake_check(up={'10.0.0.2'})
        assert tracker.run_due(db, check, clock=lambda: T0) == {'checked': 1, 'up': 0}  # only the one that isn't paced
        assert [tracker.run_paced(db, check, clock=lambda: T0) for _ in range(4)] == [(1, 0), (1, 1), (1, 0), None]
        assert check.calls == [['10.0.1.1'], ['10.0.0.1'], ['10.0.0.2'], ['10.0.0.3']]
        assert tracker.get_state(db, 'paced') == {'at': T0, 'ip': '10.0.0.3', 'up': False}

        # Check now runs one paced address right away; more are queued, so they stay apart.
        ids = ids_by_ip(db)
        assert tracker.check_now(db, [ids['10.0.0.1']], check, clock=lambda: T0 + 60) == {'checked': 1, 'up': 0}
        assert tracker.check_now(db, [ids['10.0.0.1'], ids['10.0.0.2']], check) == {'queued': 2, 'paced': 2}
        assert tracker.summary(db, now=T0 + 120)['pacedDue'] == 2


def test_scheduler_waits_a_random_gap_between_paced_checks(app, monkeypatch):
    monkeypatch.setattr(probe, 'check', lambda ips, tcp_fallback=True: {ip: (False, None, '') for ip in ips})
    monkeypatch.setattr(probe, 'self_test', lambda: {'icmp': False, 'tcp': False})
    sleeps = []
    monkeypatch.setattr(scheduler.time, 'sleep', sleeps.append)
    with app.app_context():
        db = get_db()
        tracker.add_scan(db, tracker.parse_targets('10.0.0.0/29')[0], '10.0.0.0/29', paced=True)
        tracker.add(db, tracker.parse_targets('10.0.1.1')[0])
    scheduler.run(app.config['DATABASE'], once=True)
    with app.app_context():
        assert tracker.counts(get_db())['down'] == 7 and tracker.get_state(get_db(), 'cycle')['checked'] == 1
    # One pause after each of the six paced checks, each a random 0.5 to 4 seconds.
    assert len(sleeps) == 6 and all(0.5 <= s <= 4 for s in sleeps) and len(set(sleeps)) > 1


def test_migrate_adds_scan_columns(tmp_path):
    # A database from the version before scans.
    path = tmp_path / 'old.sqlite'
    con = sqlite3.connect(path)
    con.executescript('''CREATE TABLE track_targets (id INTEGER PRIMARY KEY, ip TEXT NOT NULL UNIQUE, n INTEGER NOT NULL,
        label TEXT NOT NULL DEFAULT '', every INTEGER NOT NULL, paused INTEGER NOT NULL DEFAULT 0, added_at INTEGER NOT NULL,
        next_at INTEGER NOT NULL DEFAULT 0, last_at INTEGER, last_up INTEGER, last_up_at INTEGER, last_rtt REAL,
        last_via TEXT NOT NULL DEFAULT '', checks INTEGER NOT NULL DEFAULT 0, ups INTEGER NOT NULL DEFAULT 0);
        INSERT INTO track_targets (ip, n, label, every, added_at) VALUES ('10.0.0.1', 167772161, 'old', 86400, 1);''')
    con.close()
    client = make_app(path).test_client()
    rows = client.get('/api/tracker/targets').get_json()['rows']
    assert [(r['ip'], r['label'], r['paced'], r['scanId']) for r in rows] == [('10.0.0.1', 'old', False, None)]
    assert client.post('/api/tracker/scans', json={'ips': '10.0.0.0/30'}).get_json()['joined'] == 1


def test_lease(app):
    with app.app_context():
        db = get_db()
        assert scheduler.take_lease(db, 'a', T0) and scheduler.take_lease(db, 'a', T0 + 10)
        assert not scheduler.take_lease(db, 'b', T0 + 20)
        assert scheduler.take_lease(db, 'b', T0 + 10 + scheduler.LEASE + 1)
        assert not scheduler.take_lease(db, 'a', T0 + 10 + scheduler.LEASE + 2)


def test_release_lease_on_exit(app, monkeypatch):
    monkeypatch.setattr(scheduler, '_started', True)
    with app.app_context():
        db = get_db()
        scheduler.take_lease(db, 'another-process', T0)
        scheduler.release(app)  # not ours: kept
        assert tracker.get_state(db, 'lease')['owner'] == 'another-process'
        scheduler.take_lease(db, scheduler._owner(), T0 + scheduler.LEASE + 1)
        scheduler.release(app)
        assert tracker.get_state(db, 'lease') is None


def test_scheduler_cycle(app, monkeypatch):
    monkeypatch.setattr(probe, 'check', lambda ips, tcp_fallback=True: {ip: (ip.endswith('.1'), None, '') for ip in ips})
    monkeypatch.setattr(probe, 'self_test', lambda: {'icmp': False, 'tcp': True})
    with app.app_context():
        db = get_db()
        tracker.add(db, tracker.parse_targets('10.0.0.1 10.0.0.2')[0])
    scheduler.run(app.config['DATABASE'], once=True)
    with app.app_context():
        s = tracker.summary(get_db())
        assert s['counts']['up'] == 1 and s['counts']['down'] == 1 and s['cycle']['checked'] == 2
        assert s['heartbeat'] and s['selfTest']['tcp'] is True and not tracker.tcp_ok(get_db())


def test_fping_parse_and_fallback(monkeypatch):
    out = '10.0.0.1 is alive (0.41 ms)\n10.0.0.2 is unreachable\n10.0.0.3 is alive (12.0 ms)\n'
    assert probe.parse_fping(out) == {'10.0.0.1': 0.41, '10.0.0.3': 12.0}

    monkeypatch.setattr(probe, 'fping_path', lambda: '/usr/bin/fping')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, out, ''))
    monkeypatch.setattr(probe, 'tcp', lambda ips, ports=None: {'10.0.0.2': ('tcp/22 refused', 3.0)})
    assert probe.check(['10.0.0.1', '10.0.0.2', '10.0.0.4']) == {
        '10.0.0.1': (True, 0.41, 'icmp'), '10.0.0.2': (True, 3.0, 'tcp/22 refused'), '10.0.0.4': (False, None, '')}
    assert probe.check(['10.0.0.2'], tcp_fallback=False) == {'10.0.0.2': (False, None, '')}

    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 4, '', "can't create socket"))
    with pytest.raises(probe.ProbeError, match="can't create socket"):
        probe.check(['10.0.0.1'])

    # Without fping only TCP is used.
    monkeypatch.setattr(probe, 'fping_path', lambda: None)
    assert probe.check(['10.0.0.2', '10.0.0.4'])['10.0.0.2'] == (True, 3.0, 'tcp/22 refused')
    with pytest.raises(probe.ProbeError):
        probe.check(['10.0.0.2'], tcp_fallback=False)


def test_tcp_probe_against_local_socket():
    import socket
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        assert probe.tcp(['127.0.0.1'], ports=[port]) == {'127.0.0.1': (f'tcp/{port}', pytest.approx(0, abs=1000))}
    finally:
        srv.close()


def test_init_db_if_empty_keeps_tracked_ips(tmp_path):
    app = make_app(tmp_path / 'db.sqlite')
    app.test_client().post('/api/tracker/targets', json={'ips': '10.0.0.1'})
    out = app.test_cli_runner().invoke(args=['init-db', '--if-empty'])
    assert 'keeping it' in out.output
    with app.app_context():
        assert tracker.counts(get_db())['all'] == 1
