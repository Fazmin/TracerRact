"""IP tracker: addresses checked on a schedule to see whether they are still in use.

Each target is checked every `every` seconds by the scheduler (scheduler.py) with probe.check. Its
status is worked out when it is read, so an address moves to 'unused' as soon as it has gone
UNUSED_DAYS without a reply since tracking started, even between checks. Explorer details (host,
building, room, owner) are joined live by address, so they follow every new import.
"""
import csv
import io
import ipaddress
import json
import os
import re
import time
from datetime import datetime, timezone

from . import probe
from .db import transaction
from .util import cidr_range, ipn

DAY = 86400
INTERVALS = [('4h', 4 * 3600, 'Every 4 hours'), ('8h', 8 * 3600, 'Every 8 hours'), ('12h', 12 * 3600, 'Every 12 hours'),
             ('1d', DAY, 'Every day'), ('3d', 3 * DAY, 'Every 3 days'), ('1mo', 30 * DAY, 'Every month'),
             ('2mo', 60 * DAY, 'Every 2 months')]
EVERY = {k: s for k, s, _ in INTERVALS}
EVERY_LABEL = {s: l for _, s, l in INTERVALS}
DEFAULT_EVERY = '1d'
UNUSED_DAYS = int(os.environ.get('TRACKER_UNUSED_DAYS') or 90)
MAX_ADD = 10_000
CHUNK = 256            # targets per probe run; the scheduler renews its lease between chunks
INLINE_CHECK = 25      # "Check now" for up to this many runs in the request, more are queued
KEEP_DAYS = 400        # check history kept per target
PROFILE_DAYS = 180
RECENT = 20            # results shown as ticks in the list
STATUS_LABEL = {'up': 'Up', 'down': 'Down', 'unused': 'Likely unused', 'pending': 'Pending', 'paused': 'Paused'}
STATUS_ORDER = ['unused', 'down', 'up', 'pending', 'paused']

_SPLIT = re.compile(r'[\s,;]+')
_CIDR = re.compile(r'\d+\.\d+\.\d+\.\d+/\d+', re.ASCII)
_RANGE = re.compile(r'\d+\.\d+\.\d+\.\d+-\d+\.\d+\.\d+\.\d+', re.ASCII)
_CTX = ('track_targets t LEFT JOIN assets a ON a.id = (SELECT MIN(asset_id) FROM asset_ips WHERE n = t.n)')
_COLS = ("t.*, a.id AS asset_id, COALESCE(NULLIF(a.host, ''), a.an, '') AS host, COALESCE(a.bld, '') AS bld, "
         "COALESCE(a.roomL, '') AS room, COALESCE(a.owner, '') AS owner, COALESCE(a.type, '') AS type, "
         "COALESCE(a.cov, '') AS cov, COALESCE(a.state, '') AS state")


def ip_str(n):
    return str(ipaddress.IPv4Address(n))


def every_seconds(key):
    if key not in EVERY:
        raise ValueError('Choose how often to check: ' + ', '.join(EVERY) + '.')
    return EVERY[key]


def _now(now):
    return int(time.time() if now is None else now)


def _ids(ids):
    try:
        return json.dumps([int(i) for i in ids or []])
    except (TypeError, ValueError):
        raise ValueError('ids must be a list of numbers.') from None


def _too_many():
    return ValueError(f'That is more than {MAX_ADD:,} addresses. Add at most {MAX_ADD:,} at a time.')


# -- state (scheduler lease, last cycle, self-test) -----------------------------------------------
def get_state(db, key):
    row = db.execute('SELECT value FROM tracker_state WHERE key = ?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_state(db, key, value):
    db.execute('INSERT OR REPLACE INTO tracker_state (key, value) VALUES (?, ?)', (key, json.dumps(value)))


def tcp_ok(db):
    """False once the self-test saw TCP answers for an address that cannot be in use."""
    return not (get_state(db, 'selftest') or {}).get('tcp')


def prober(db):
    """probe.check with the TCP fallback on or off as the self-test decided."""
    tcp = tcp_ok(db)
    return lambda ips: probe.check(ips, tcp_fallback=tcp)


# -- adding and changing targets ------------------------------------------------------------------
def parse_targets(text):
    """IPs, CIDR blocks and a-b ranges, separated by spaces, commas, semicolons or new lines, to
    (sorted unique addresses as integers, tokens that are not addresses). Blocks of four addresses or
    more skip their network and broadcast addresses."""
    nums, bad = set(), []
    for tok in _SPLIT.split(text or ''):
        if not tok:
            continue
        if _CIDR.fullmatch(tok):
            r = cidr_range(tok)
            if not r:
                bad.append(tok)
                continue
            lo, hi = r
            if hi - lo >= 3:
                lo, hi = lo + 1, hi - 1
        elif _RANGE.fullmatch(tok):
            lo, hi = (ipn(x) for x in tok.split('-'))
            if lo < 0 or hi < lo:
                bad.append(tok)
                continue
        else:
            lo = hi = ipn(tok)
            if lo < 0:
                bad.append(tok)
                continue
        if hi - lo + 1 > MAX_ADD:
            raise _too_many()
        nums.update(range(lo, hi + 1))
        if len(nums) > MAX_ADD:
            raise _too_many()
    return sorted(nums), bad


def add(db, nums, every=DEFAULT_EVERY, label='', now=None):
    """Start tracking these addresses (integers); they are checked on the scheduler's next tick.
    Addresses already tracked keep their settings and history."""
    if len(nums) > MAX_ADD:
        raise _too_many()
    secs, now, label = every_seconds(every), _now(now), (label or '').strip()[:200]
    with transaction(db):
        before = db.total_changes
        db.executemany('INSERT OR IGNORE INTO track_targets (ip, n, label, every, added_at, next_at) VALUES (?, ?, ?, ?, ?, 0)',
                       ((ip_str(n), n, label, secs, now) for n in nums))
        added = db.total_changes - before
    return {'added': added, 'existing': len(nums) - added}


def update(db, ids, every=None, paused=None, label=None):
    sets, params = [], []
    if every is not None:
        secs = every_seconds(every)
        # The next check moves with the interval; if that time has passed it is due now.
        sets += ['every = ?', 'next_at = CASE WHEN last_at IS NULL THEN next_at ELSE last_at + ? END']
        params += [secs, secs]
    if paused is not None:
        sets.append('paused = ?')
        params.append(int(bool(paused)))
    if label is not None:
        sets.append('label = ?')
        params.append(str(label).strip()[:200])
    if not sets:
        return {'updated': 0}
    cur = db.execute(f"UPDATE track_targets SET {', '.join(sets)} WHERE id IN (SELECT value FROM json_each(?))",
                     params + [_ids(ids)])
    return {'updated': cur.rowcount}


def remove(db, ids):
    with transaction(db):
        cur = db.execute('DELETE FROM track_targets WHERE id IN (SELECT value FROM json_each(?))', (_ids(ids),))
    return {'removed': cur.rowcount}


# -- checking -------------------------------------------------------------------------------------
def run_targets(db, rows, check, clock=time.time):
    """Check these targets (rows with id and ip) with check(ips) and record the results.
    Returns (checked, up)."""
    res = check([r['ip'] for r in rows])
    now, checked, ups = int(clock()), 0, 0
    with transaction(db):
        for r in rows:
            up, rtt, via = res.get(r['ip'], (False, None, ''))
            up = int(bool(up))
            cur = db.execute('UPDATE track_targets SET last_at = ?, last_up = ?, last_rtt = ?, last_via = ?, '
                             'last_up_at = CASE WHEN ? THEN ? ELSE last_up_at END, checks = checks + 1, ups = ups + ?, '
                             'next_at = ? + every WHERE id = ?', (now, up, rtt, via, up, now, up, now, r['id']))
            if cur.rowcount:  # removed while it was being checked
                db.execute('INSERT INTO track_checks (target_id, at, up, rtt, via) VALUES (?, ?, ?, ?, ?)',
                           (r['id'], now, up, rtt, via))
                checked += 1
                ups += up
    return checked, ups


def run_due(db, check, clock=time.time, renew=None):
    """Check every unpaused target that is due, CHUNK at a time. renew() runs between chunks; when it
    returns False (the scheduler lost its lease) the run stops."""
    checked = ups = 0
    while True:
        rows = db.execute('SELECT id, ip FROM track_targets WHERE paused = 0 AND next_at <= ? ORDER BY next_at, id LIMIT ?',
                          (int(clock()), CHUNK)).fetchall()
        if not rows:
            break
        c, u = run_targets(db, rows, check, clock)
        checked, ups = checked + c, ups + u
        if renew and not renew():
            break
    return {'checked': checked, 'up': ups}


def check_now(db, ids, check, clock=time.time):
    """Check a few targets right away (paused ones too); queue larger requests for the scheduler."""
    idj = _ids(ids)
    rows = db.execute('SELECT id, ip FROM track_targets WHERE id IN (SELECT value FROM json_each(?))', (idj,)).fetchall()
    if len(rows) <= INLINE_CHECK:
        c, u = run_targets(db, rows, check, clock)
        return {'checked': c, 'up': u}
    cur = db.execute('UPDATE track_targets SET next_at = 0 WHERE paused = 0 AND id IN (SELECT value FROM json_each(?))', (idj,))
    return {'queued': cur.rowcount}


def prune(db, now=None):
    db.execute('DELETE FROM track_checks WHERE at < ?', (_now(now) - KEEP_DAYS * DAY,))


# -- reading --------------------------------------------------------------------------------------
def status_sql(now):
    cutoff = int(now) - UNUSED_DAYS * DAY
    return ("CASE WHEN t.paused THEN 'paused' WHEN t.last_at IS NULL THEN 'pending' WHEN t.last_up THEN 'up' "
            f"WHEN COALESCE(t.last_up_at, t.added_at) <= {cutoff} THEN 'unused' ELSE 'down' END")


def _status_rank(now):
    return 'CASE (' + status_sql(now) + ') ' + ' '.join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(STATUS_ORDER)) + ' END'


SORTS = {'ip': 't.n', 'last': 't.last_at', 'reply': 't.last_up_at', 'added': 't.added_at', 'every': 't.every',
         'uptime': 'CAST(t.ups AS REAL) / NULLIF(t.checks, 0)', 'label': 't.label COLLATE NATURAL',
         'host': 'host COLLATE NATURAL', 'bld': 'bld COLLATE NATURAL', 'room': 'room COLLATE NATURAL',
         'owner': 'owner COLLATE NATURAL'}


def _where(q, bucket, now):
    clauses, params = [], []
    if bucket in STATUS_LABEL:
        clauses.append(f'({status_sql(now)}) = ?')
        params.append(bucket)
    for tok in (q or '').lower().split():
        if _CIDR.fullmatch(tok) or _RANGE.fullmatch(tok):
            r = cidr_range(tok) if '/' in tok else tuple(ipn(x) for x in tok.split('-'))
            if not r or min(r) < 0:
                clauses.append('0')
                continue
            clauses.append('t.n BETWEEN ? AND ?')
            params += list(r)
        else:
            clauses.append("instr(lower(t.ip || ' ' || t.label || ' ' || COALESCE(a.host, '') || ' ' || COALESCE(a.an, '') "
                           "|| ' ' || COALESCE(a.bld, '') || ' ' || COALESCE(a.roomL, '') || ' ' || COALESCE(a.owner, '')), ?) > 0")
            params.append(tok)
    return ' AND '.join(clauses) or '1', params


def _row(r):
    return {'id': r['id'], 'ip': r['ip'], 'label': r['label'], 'every': r['every'],
            'everyLabel': EVERY_LABEL.get(r['every'], f"Every {r['every'] / 3600:g} hours"), 'paused': bool(r['paused']),
            'status': r['status'], 'addedAt': r['added_at'], 'nextAt': r['next_at'], 'lastAt': r['last_at'],
            'lastUp': r['last_up'], 'lastUpAt': r['last_up_at'], 'lastRtt': r['last_rtt'], 'lastVia': r['last_via'],
            'checks': r['checks'], 'ups': r['ups'], 'assetId': r['asset_id'], 'host': r['host'], 'bld': r['bld'],
            'room': r['room'], 'owner': r['owner'], 'type': r['type'], 'cov': r['cov'], 'state': r['state']}


def _select(db, q, bucket, now, sort='ip', direction=1, offset=0, limit=None):
    where, params = _where(q, bucket, now)
    order = SORTS.get(sort) or (_status_rank(now) if sort == 'status' else 't.n')
    d = 'DESC' if direction < 0 else 'ASC'
    sql = f'SELECT {_COLS}, ({status_sql(now)}) AS status FROM {_CTX} WHERE {where} ORDER BY {order} {d}, t.n {d}'
    if limit is not None:
        sql += ' LIMIT ? OFFSET ?'
        params = params + [limit, offset]
    return db.execute(sql, params).fetchall()


def targets(db, bucket='', q='', sort='ip', direction=1, offset=0, limit=200, now=None):
    """One page of tracked addresses, each with its last RECENT results ('1' answered, '0' not), oldest first."""
    now = _now(now)
    rows = [_row(r) for r in _select(db, q, bucket, now, sort, direction, offset, limit)]
    recent = {}
    if rows:
        for tid, up in db.execute(
                'SELECT target_id, up FROM (SELECT target_id, up, at, ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY at DESC) AS k '
                'FROM track_checks WHERE target_id IN (SELECT value FROM json_each(?))) WHERE k <= ? ORDER BY target_id, at',
                (json.dumps([r['id'] for r in rows]), RECENT)):
            recent[tid] = recent.get(tid, '') + str(up)
    for r in rows:
        r['recent'] = recent.get(r['id'], '')
    where, params = _where(q, bucket, now)
    count = db.execute(f'SELECT COUNT(*) FROM {_CTX} WHERE {where}', params).fetchone()[0]
    return {'rows': rows, 'count': count, 'offset': offset}


def counts(db, now=None):
    out = {s: 0 for s in STATUS_LABEL}
    for s, n in db.execute(f'SELECT ({status_sql(_now(now))}) AS s, COUNT(*) FROM track_targets t GROUP BY s'):
        out[s] = n
    out['all'] = sum(out.values())
    return out


def summary(db, now=None):
    now = _now(now)
    lease = get_state(db, 'lease') or {}
    nxt = db.execute('SELECT MIN(next_at) FROM track_targets WHERE paused = 0').fetchone()[0]
    return {'now': now, 'counts': counts(db, now), 'unusedDays': UNUSED_DAYS, 'defaultEvery': DEFAULT_EVERY,
            'intervals': [{'k': k, 's': s, 'l': l} for k, s, l in INTERVALS], 'statuses': STATUS_LABEL,
            'scanner': probe.scanner(tcp_ok(db)), 'selfTest': get_state(db, 'selftest'),
            'heartbeat': lease.get('at'), 'cycle': get_state(db, 'cycle'), 'nextAt': nxt}


def profile(db, tid, now=None):
    now = _now(now)
    rows = db.execute(f'SELECT {_COLS}, ({status_sql(now)}) AS status FROM {_CTX} WHERE t.id = ?', (tid,)).fetchall()
    if not rows:
        return None
    t = _row(rows[0])
    if t['status'] == 'down':
        t['unusedAt'] = (t['lastUpAt'] or t['addedAt']) + UNUSED_DAYS * DAY
    checks = db.execute('SELECT at, up, rtt, via FROM track_checks WHERE target_id = ? AND at >= ? ORDER BY at DESC LIMIT 2000',
                        (tid, now - PROFILE_DAYS * DAY)).fetchall()
    return {'target': t, 'checks': [list(c) for c in checks], 'days': PROFILE_DAYS, 'unusedDays': UNUSED_DAYS}


def for_ip(db, ip, now=None):
    """The tracking of one address, for the asset detail panel, or None."""
    n = ipn(ip)
    if n < 0:
        return None
    now = _now(now)
    r = db.execute(f'SELECT id, every, last_at, last_up_at, ({status_sql(now)}) AS status FROM track_targets t WHERE t.n = ?',
                   (n,)).fetchone()
    return {'id': r['id'], 'status': r['status'], 'everyLabel': EVERY_LABEL.get(r['every'], ''), 'lastAt': r['last_at'],
            'lastUpAt': r['last_up_at']} if r else None


def _when(s):
    return datetime.fromtimestamp(s, timezone.utc).strftime('%Y-%m-%d %H:%M') if s else ''


EXPORT_COLUMNS = ['IP address', 'Status', 'Last reply (UTC)', 'Last check (UTC)', 'Tracked since (UTC)', 'Checks', 'Replies',
                  'Interval', 'Host', 'Building', 'Room', 'Owner', 'BlueCat state', 'Coverage', 'Label']


def export_csv(db, bucket='', q='', sort='ip', direction=1, now=None):
    now = _now(now)
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator='\n')
    w.writerow(EXPORT_COLUMNS)
    for r in (_row(x) for x in _select(db, q, bucket, now, sort, direction)):
        w.writerow([r['ip'], STATUS_LABEL[r['status']], _when(r['lastUpAt']), _when(r['lastAt']), _when(r['addedAt']),
                    r['checks'], r['ups'], r['everyLabel'], r['host'], r['bld'], r['room'], r['owner'], r['state'],
                    r['cov'], r['label']])
    return buf.getvalue().rstrip('\n')
