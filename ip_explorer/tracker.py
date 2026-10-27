"""IP tracker: addresses checked on a schedule to see whether they are still in use.

Addresses are tracked on their own or as part of a scan: a set added together, such as a /24, with one
schedule for all of them. The scheduler (scheduler.py) checks each address every `every` seconds with
probe.check, many at once, or, for paced addresses, one at a time with a random PACE_GAP between them.
Status is worked out when it is read, so an address that stops answering moves through the SILENT_DAYS
buckets (no reply for 30+ days, 60+ days and so on) as time passes, even between checks. Explorer
details (host, building, room, owner) are joined live by address, so they follow every new import.
"""
import csv
import io
import ipaddress
import json
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
# Buckets for addresses that do not answer, by days since their last reply (or since tracking started).
SILENT_LABEL = {30: '30+ days', 60: '60+ days', 90: '90+ days', 180: '6+ months', 365: '12+ months'}
SILENT_DAYS = list(SILENT_LABEL)
SILENT_BUCKETS = {f's{d}': d for d in SILENT_DAYS}
PACE_GAP = (0.5, 4.0)  # seconds between paced checks, picked at random each time
MAX_ADD = 10_000
CHUNK = 256            # targets per probe run; the scheduler renews its lease between chunks
INLINE_CHECK = 25      # "Check now" for up to this many runs in the request, more are queued
KEEP_DAYS = 400        # check history kept per target
PROFILE_DAYS = 180
RECENT = 20            # results shown as ticks in the list
STATUS_LABEL = {'up': 'Up', 'down': 'Down', 'pending': 'Pending', 'paused': 'Paused'}
BUCKET_LABEL = {**STATUS_LABEL, **{k: 'No reply ' + SILENT_LABEL[d] for k, d in SILENT_BUCKETS.items()}}
STATUS = "CASE WHEN t.paused THEN 'paused' WHEN t.last_at IS NULL THEN 'pending' WHEN t.last_up THEN 'up' ELSE 'down' END"

_SPLIT = re.compile(r'[\s,;]+')
_CIDR = re.compile(r'\d+\.\d+\.\d+\.\d+/\d+', re.ASCII)
_RANGE = re.compile(r'\d+\.\d+\.\d+\.\d+-\d+\.\d+\.\d+\.\d+', re.ASCII)
_CTX = ('track_targets t LEFT JOIN track_scans s ON s.id = t.scan_id '
        'LEFT JOIN assets a ON a.id = (SELECT MIN(asset_id) FROM asset_ips WHERE n = t.n)')
_COLS = ("t.*, COALESCE(s.name, '') AS scan, a.id AS asset_id, COALESCE(NULLIF(a.host, ''), a.an, '') AS host, "
         "COALESCE(a.bld, '') AS bld, COALESCE(a.roomL, '') AS room, COALESCE(a.owner, '') AS owner, "
         "COALESCE(a.type, '') AS type, COALESCE(a.cov, '') AS cov, COALESCE(a.state, '') AS state")


def ip_str(n):
    return str(ipaddress.IPv4Address(n))


def every_seconds(key):
    if key not in EVERY:
        raise ValueError('Choose how often to check: ' + ', '.join(EVERY) + '.')
    return EVERY[key]


def every_label(secs):
    return EVERY_LABEL.get(secs, f'Every {secs / 3600:g} hours')


def _now(now):
    return int(time.time() if now is None else now)


def _ids(ids):
    try:
        return json.dumps([int(i) for i in ids or []])
    except (TypeError, ValueError):
        raise ValueError('ids must be a list of numbers.') from None


def _too_many():
    return ValueError(f'That is more than {MAX_ADD:,} addresses. Add at most {MAX_ADD:,} at a time.')


# -- state (scheduler lease, last cycle, last paced check, self-test) -----------------------------
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


def spec_of(text, bad=()):
    """The addresses, ranges and blocks that were entered, on one line, without the invalid ones."""
    bad = set(bad)
    return ' '.join(t for t in _SPLIT.split(text or '') if t and t not in bad)


def add(db, nums, every=DEFAULT_EVERY, label='', paced=False, now=None):
    """Start tracking these addresses (integers) on their own; they are checked on the scheduler's
    next tick. Addresses already tracked keep their settings and history."""
    if len(nums) > MAX_ADD:
        raise _too_many()
    secs, now, label = every_seconds(every), _now(now), (label or '').strip()[:200]
    with transaction(db):
        before = db.total_changes
        db.executemany('INSERT OR IGNORE INTO track_targets (ip, n, label, every, paced, added_at, next_at) '
                       'VALUES (?, ?, ?, ?, ?, ?, 0)', ((ip_str(n), n, label, secs, int(bool(paced)), now) for n in nums))
        added = db.total_changes - before
    return {'added': added, 'existing': len(nums) - added}


def add_scan(db, nums, spec, name='', every=DEFAULT_EVERY, paced=False, now=None):
    """Start a scan of these addresses (integers). New addresses are checked from the scheduler's next
    tick. Addresses tracked on their own join the scan and take its schedule, keeping their history;
    addresses in another scan stay there."""
    if len(nums) > MAX_ADD:
        raise _too_many()
    secs, now, paced = every_seconds(every), _now(now), int(bool(paced))
    spec = (spec or '').strip()[:2000]
    name = (name or '').strip()[:200] or _short(spec)
    with transaction(db):
        sid = db.execute('INSERT INTO track_scans (name, spec, every, paced, added_at) VALUES (?, ?, ?, ?, ?)',
                         (name, spec, secs, paced, now)).lastrowid
        joined = db.execute('UPDATE track_targets SET scan_id = ?, every = ?, paced = ?, paused = 0, '
                            'next_at = CASE WHEN last_at IS NULL THEN next_at ELSE last_at + ? END '
                            'WHERE scan_id IS NULL AND n IN (SELECT value FROM json_each(?))',
                            (sid, secs, paced, secs, json.dumps(nums))).rowcount
        before = db.total_changes
        db.executemany('INSERT OR IGNORE INTO track_targets (ip, n, every, paced, scan_id, added_at, next_at) '
                       'VALUES (?, ?, ?, ?, ?, ?, 0)', ((ip_str(n), n, secs, paced, sid, now) for n in nums))
        added = db.total_changes - before
        if not added and not joined:
            raise ValueError('All of these addresses are already in other scans.')
    return {'scan': sid, 'added': added, 'joined': joined, 'existing': len(nums) - added - joined}


def _short(spec):
    toks = spec.split()
    if not toks:
        return 'Scan'
    return toks[0] + (f' and {len(toks) - 1} more' if len(toks) > 1 else '')


def _target_sets(every=None, paused=None, paced=None, label=None):
    sets, params = [], []
    if every is not None:
        secs = every_seconds(every)
        # The next check moves with the interval; if that time has passed it is due now.
        sets += ['every = ?', 'next_at = CASE WHEN last_at IS NULL THEN next_at ELSE last_at + ? END']
        params += [secs, secs]
    if paused is not None:
        sets.append('paused = ?')
        params.append(int(bool(paused)))
    if paced is not None:
        sets.append('paced = ?')
        params.append(int(bool(paced)))
    if label is not None:
        sets.append('label = ?')
        params.append(str(label).strip()[:200])
    return sets, params


def update(db, ids, every=None, paused=None, paced=None, label=None):
    sets, params = _target_sets(every, paused, paced, label)
    if not sets:
        return {'updated': 0}
    cur = db.execute(f"UPDATE track_targets SET {', '.join(sets)} WHERE id IN (SELECT value FROM json_each(?))",
                     params + [_ids(ids)])
    return {'updated': cur.rowcount}


def update_scan(db, sid, name=None, every=None, paused=None, paced=None):
    """Change a scan's settings. The interval, pacing and pausing apply to every address in it.
    None when there is no such scan."""
    sets, params = [], []
    if name is not None:
        name = str(name).strip()[:200]
        if not name:
            raise ValueError('Give the scan a name.')
        sets.append('name = ?')
        params.append(name)
    for col, v in (('every', every_seconds(every) if every is not None else None),
                   ('paused', None if paused is None else int(bool(paused))),
                   ('paced', None if paced is None else int(bool(paced)))):
        if v is not None:
            sets.append(f'{col} = ?')
            params.append(v)
    tsets, tparams = _target_sets(every, paused, paced)
    with transaction(db):
        if not db.execute('SELECT 1 FROM track_scans WHERE id = ?', (sid,)).fetchone():
            return None
        if sets:
            db.execute(f"UPDATE track_scans SET {', '.join(sets)} WHERE id = ?", params + [sid])
        n = db.execute(f"UPDATE track_targets SET {', '.join(tsets)} WHERE scan_id = ?", tparams + [sid]).rowcount if tsets else 0
    return {'updated': n}


def remove(db, ids):
    with transaction(db):
        cur = db.execute('DELETE FROM track_targets WHERE id IN (SELECT value FROM json_each(?))', (_ids(ids),))
        # A scan goes when its last address does.
        db.execute('DELETE FROM track_scans WHERE id NOT IN (SELECT scan_id FROM track_targets WHERE scan_id IS NOT NULL)')
    return {'removed': cur.rowcount}


def remove_scan(db, sid):
    """Stop tracking a scan's addresses and delete their history. None when there is no such scan."""
    with transaction(db):
        n = db.execute('SELECT COUNT(*) FROM track_targets WHERE scan_id = ?', (sid,)).fetchone()[0]
        if not db.execute('DELETE FROM track_scans WHERE id = ?', (sid,)).rowcount:
            return None
    return {'removed': n}


def scan_target_ids(db, sid):
    return [r[0] for r in db.execute('SELECT id FROM track_targets WHERE scan_id = ?', (sid,))]


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
    """Check every unpaused, unpaced target that is due, CHUNK at a time. renew() runs between chunks;
    when it returns False (the scheduler lost its lease) the run stops."""
    checked = ups = 0
    while True:
        rows = db.execute('SELECT id, ip FROM track_targets WHERE paused = 0 AND paced = 0 AND next_at <= ? '
                          'ORDER BY next_at, id LIMIT ?', (int(clock()), CHUNK)).fetchall()
        if not rows:
            break
        c, u = run_targets(db, rows, check, clock)
        checked, ups = checked + c, ups + u
        if renew and not renew():
            break
    return {'checked': checked, 'up': ups}


def run_paced(db, check, clock=time.time):
    """Check the paced target that has waited longest, if one is due. The scheduler waits a random
    PACE_GAP between calls. Returns (checked, up), or None when nothing paced is due."""
    row = db.execute('SELECT id, ip FROM track_targets WHERE paused = 0 AND paced = 1 AND next_at <= ? '
                     'ORDER BY next_at, n LIMIT 1', (int(clock()),)).fetchone()
    if not row:
        return None
    c, u = run_targets(db, [row], check, clock)
    set_state(db, 'paced', {'at': int(clock()), 'ip': row['ip'], 'up': bool(u)})
    return c, u


def check_now(db, ids, check, clock=time.time):
    """Check a few targets right away (paused ones too). Larger requests, and more than one paced
    target, are queued for the scheduler, which keeps paced checks apart."""
    idj = _ids(ids)
    rows = db.execute('SELECT id, ip, paced FROM track_targets WHERE id IN (SELECT value FROM json_each(?))', (idj,)).fetchall()
    if len(rows) <= INLINE_CHECK and (len(rows) == 1 or not any(r['paced'] for r in rows)):
        c, u = run_targets(db, rows, check, clock)
        return {'checked': c, 'up': u}
    with transaction(db):
        cur = db.execute('UPDATE track_targets SET next_at = 0 WHERE paused = 0 AND id IN (SELECT value FROM json_each(?))', (idj,))
        paced = db.execute('SELECT COUNT(*) FROM track_targets WHERE paused = 0 AND paced = 1 AND id IN (SELECT value FROM json_each(?))',
                           (idj,)).fetchone()[0]
    return {'queued': cur.rowcount, 'paced': paced}


def prune(db, now=None):
    db.execute('DELETE FROM track_checks WHERE at < ?', (_now(now) - KEEP_DAYS * DAY,))


# -- reading --------------------------------------------------------------------------------------
def silent_sql(now):
    """The longest SILENT_DAYS a down address has gone without a reply (since tracking started if it
    never answered), else 0."""
    since = 'COALESCE(t.last_up_at, t.added_at)'
    tiers = ' '.join(f'WHEN {since} <= {int(now) - d * DAY} THEN {d}' for d in reversed(SILENT_DAYS))
    return f"CASE WHEN ({STATUS}) = 'down' THEN CASE {tiers} ELSE 0 END ELSE 0 END"


def _status_rank(now):
    # Longest silence first, then down, up, pending, paused.
    return f"CASE ({STATUS}) WHEN 'down' THEN -({silent_sql(now)}) WHEN 'up' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END"


SORTS = {'ip': 't.n', 'last': 't.last_at', 'reply': 't.last_up_at', 'added': 't.added_at', 'every': 't.every',
         'uptime': 'CAST(t.ups AS REAL) / NULLIF(t.checks, 0)', 'label': 't.label COLLATE "NATURAL"',
         'scan': 'scan COLLATE "NATURAL"', 'host': 'host COLLATE "NATURAL"', 'bld': 'bld COLLATE "NATURAL"',
         'room': 'room COLLATE "NATURAL"', 'owner': 'owner COLLATE "NATURAL"'}


def _where(q, bucket, now, scan=''):
    clauses, params = [], []
    if bucket in STATUS_LABEL:
        clauses.append(f'({STATUS}) = ?')
        params.append(bucket)
    elif bucket in SILENT_BUCKETS:
        clauses.append(f'({silent_sql(now)}) >= ?')
        params.append(SILENT_BUCKETS[bucket])
    if scan == 'none':
        clauses.append('t.scan_id IS NULL')
    elif scan:
        try:
            params.append(int(scan))
        except ValueError:
            raise ValueError('scan must be a scan id or none.') from None
        clauses.append('t.scan_id = ?')
    for tok in (q or '').lower().split():
        if _CIDR.fullmatch(tok) or _RANGE.fullmatch(tok):
            r = cidr_range(tok) if '/' in tok else tuple(ipn(x) for x in tok.split('-'))
            if not r or min(r) < 0:
                clauses.append('0')
                continue
            clauses.append('t.n BETWEEN ? AND ?')
            params += list(r)
        else:
            clauses.append("instr(lower(t.ip || ' ' || t.label || ' ' || COALESCE(s.name, '') || ' ' || COALESCE(a.host, '') "
                           "|| ' ' || COALESCE(a.an, '') || ' ' || COALESCE(a.bld, '') || ' ' || COALESCE(a.roomL, '') "
                           "|| ' ' || COALESCE(a.owner, '')), ?) > 0")
            params.append(tok)
    return ' AND '.join(clauses) or '1', params


def _row(r):
    return {'id': r['id'], 'ip': r['ip'], 'label': r['label'], 'every': r['every'], 'everyLabel': every_label(r['every']),
            'paused': bool(r['paused']), 'paced': bool(r['paced']), 'scanId': r['scan_id'], 'scan': r['scan'],
            'status': r['status'], 'silent': r['silent'], 'addedAt': r['added_at'], 'nextAt': r['next_at'],
            'lastAt': r['last_at'], 'lastUp': r['last_up'], 'lastUpAt': r['last_up_at'], 'lastRtt': r['last_rtt'],
            'lastVia': r['last_via'], 'checks': r['checks'], 'ups': r['ups'], 'assetId': r['asset_id'], 'host': r['host'],
            'bld': r['bld'], 'room': r['room'], 'owner': r['owner'], 'type': r['type'], 'cov': r['cov'], 'state': r['state']}


def _select(db, q, bucket, now, sort='ip', direction=1, offset=0, limit=None, scan='', where_id=None):
    where, params = _where(q, bucket, now, scan)
    if where_id is not None:
        where, params = 't.id = ?', [where_id]
    order = SORTS.get(sort) or (_status_rank(now) if sort == 'status' else 't.n')
    d = 'DESC' if direction < 0 else 'ASC'
    sql = (f'SELECT {_COLS}, ({STATUS}) AS status, ({silent_sql(now)}) AS silent FROM {_CTX} '
           f'WHERE {where} ORDER BY {order} {d}, t.n {d}')
    if limit is not None:
        sql += ' LIMIT ? OFFSET ?'
        params = params + [limit, offset]
    return db.execute(sql, params).fetchall()


def targets(db, bucket='', q='', sort='ip', direction=1, offset=0, limit=200, scan='', now=None):
    """One page of tracked addresses, each with its last RECENT results ('1' answered, '0' not), oldest
    first, and the counts by bucket for the same scan and search."""
    now = _now(now)
    rows = [_row(r) for r in _select(db, q, bucket, now, sort, direction, offset, limit, scan)]
    recent = {}
    if rows:
        for tid, up in db.execute(
                'SELECT target_id, up FROM (SELECT target_id, up, at, ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY at DESC) AS k '
                'FROM track_checks WHERE target_id IN (SELECT value FROM json_each(?))) WHERE k <= ? ORDER BY target_id, at',
                (json.dumps([r['id'] for r in rows]), RECENT)):
            recent[tid] = recent.get(tid, '') + str(up)
    for r in rows:
        r['recent'] = recent.get(r['id'], '')
    where, params = _where(q, bucket, now, scan)
    count = db.execute(f'SELECT COUNT(*) FROM {_CTX} WHERE {where}', params).fetchone()[0]
    return {'rows': rows, 'count': count, 'offset': offset, 'counts': counts(db, now, q, scan)}


def _tally(groups):
    """{status or silence bucket: count} from (status, silent, count) rows. The silence buckets add up:
    an address with no reply for 90+ days is in the 30+ and 60+ day buckets too."""
    out = dict.fromkeys([*STATUS_LABEL, *SILENT_BUCKETS], 0)
    for st, silent, n in groups:
        out[st] += n
        for k, d in SILENT_BUCKETS.items():
            if silent >= d:
                out[k] += n
    out['all'] = sum(out[s] for s in STATUS_LABEL)
    return out


def counts(db, now=None, q='', scan=''):
    now = _now(now)
    where, params = _where(q, '', now, scan)
    return _tally(db.execute(f'SELECT ({STATUS}), ({silent_sql(now)}), COUNT(*) FROM {_CTX} WHERE {where} GROUP BY 1, 2', params))


def scans(db, now=None, q=''):
    """Every scan with its settings and counts, by name. Addresses tracked on their own follow as a
    scan with id None, when there are any. With a search, the counts are of the addresses it finds."""
    now = _now(now)
    groups, timing = {}, {}
    where, params = _where(q, '', now)
    for sid, st, silent, n in db.execute(f'SELECT t.scan_id, ({STATUS}), ({silent_sql(now)}), COUNT(*) FROM {_CTX} '
                                         f'WHERE {where} GROUP BY 1, 2, 3', params):
        groups.setdefault(sid, []).append((st, silent, n))
    for sid, due, nxt, last in db.execute('SELECT scan_id, SUM(paused = 0 AND next_at <= ?), MIN(CASE WHEN paused = 0 THEN next_at END), '
                                          'MAX(last_at) FROM track_targets GROUP BY scan_id', (now,)):
        timing[sid] = {'due': due, 'nextAt': nxt, 'lastAt': last}
    out = [{'id': s['id'], 'name': s['name'], 'spec': s['spec'], 'every': s['every'], 'everyLabel': every_label(s['every']),
            'paced': bool(s['paced']), 'paused': bool(s['paused']), 'addedAt': s['added_at'],
            'counts': _tally(groups.get(s['id'], [])), **timing.get(s['id'], {'due': 0, 'nextAt': None, 'lastAt': None})}
           for s in db.execute('SELECT * FROM track_scans ORDER BY name COLLATE "NATURAL", id')]
    if None in groups:
        out.append({'id': None, 'name': 'Added one at a time', 'counts': _tally(groups[None]), **timing[None]})
    return out


def groups(db, q='', now=None):
    """The address list grouped by scan: each scan with the counts of the addresses this search finds
    in it, and the counts of all of them. The rows of a group come from targets(scan=...)."""
    now = _now(now)
    return {'groups': scans(db, now, q), 'counts': counts(db, now, q)}


def summary(db, now=None):
    now = _now(now)
    lease = get_state(db, 'lease') or {}
    nxt = db.execute('SELECT MIN(next_at) FROM track_targets WHERE paused = 0').fetchone()[0]
    paced_due = db.execute('SELECT COUNT(*) FROM track_targets WHERE paused = 0 AND paced = 1 AND next_at <= ?', (now,)).fetchone()[0]
    return {'now': now, 'counts': counts(db, now), 'scans': scans(db, now), 'defaultEvery': DEFAULT_EVERY,
            'intervals': [{'k': k, 's': s, 'l': l} for k, s, l in INTERVALS], 'statuses': BUCKET_LABEL,
            'silent': [{'k': k, 'd': d, 'l': SILENT_LABEL[d]} for k, d in SILENT_BUCKETS.items()],
            'paceGap': PACE_GAP, 'pacedDue': paced_due, 'paced': get_state(db, 'paced'),
            'scanner': probe.scanner(tcp_ok(db)), 'selfTest': get_state(db, 'selftest'),
            'heartbeat': lease.get('at'), 'cycle': get_state(db, 'cycle'), 'nextAt': nxt}


def profile(db, tid, now=None):
    now = _now(now)
    rows = _select(db, '', '', now, where_id=tid)
    if not rows:
        return None
    t = _row(rows[0])
    if t['status'] == 'down':
        # When it reaches the next silence bucket, unless it answers before then.
        t['silentSince'] = t['lastUpAt'] or t['addedAt']
        t['nextSilent'] = next(({'d': d, 'l': SILENT_LABEL[d], 'at': t['silentSince'] + d * DAY}
                                for d in SILENT_DAYS if d > t['silent']), None)
    checks = db.execute('SELECT at, up, rtt, via FROM track_checks WHERE target_id = ? AND at >= ? ORDER BY at DESC LIMIT 2000',
                        (tid, now - PROFILE_DAYS * DAY)).fetchall()
    return {'target': t, 'checks': [list(c) for c in checks], 'days': PROFILE_DAYS}


def for_ip(db, ip, now=None):
    """The tracking of one address, for the asset detail panel, or None."""
    n = ipn(ip)
    if n < 0:
        return None
    now = _now(now)
    r = db.execute(f'SELECT id, every, last_at, last_up_at, ({STATUS}) AS status, ({silent_sql(now)}) AS silent '
                   'FROM track_targets t WHERE t.n = ?', (n,)).fetchone()
    return {'id': r['id'], 'status': r['status'], 'silent': r['silent'], 'everyLabel': EVERY_LABEL.get(r['every'], ''),
            'lastAt': r['last_at'], 'lastUpAt': r['last_up_at']} if r else None


def _when(s):
    return datetime.fromtimestamp(s, timezone.utc).strftime('%Y-%m-%d %H:%M') if s else ''


def status_text(r):
    return BUCKET_LABEL[f"s{r['silent']}"] if r['silent'] else STATUS_LABEL[r['status']]


EXPORT_COLUMNS = ['IP address', 'Status', 'Days without a reply', 'Last reply (UTC)', 'Last check (UTC)', 'Tracked since (UTC)',
                  'Checks', 'Replies', 'Interval', 'Paced', 'Scan', 'Host', 'Building', 'Room', 'Owner', 'BlueCat state',
                  'Coverage', 'Label']


def export_csv(db, bucket='', q='', sort='ip', direction=1, scan='', now=None):
    now = _now(now)
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator='\n')
    w.writerow(EXPORT_COLUMNS)
    for r in (_row(x) for x in _select(db, q, bucket, now, sort, direction, scan=scan)):
        days = (now - (r['lastUpAt'] or r['addedAt'])) // DAY if r['status'] == 'down' else ''
        w.writerow([r['ip'], status_text(r), days, _when(r['lastUpAt']), _when(r['lastAt']), _when(r['addedAt']),
                    r['checks'], r['ups'], r['everyLabel'], 'Yes' if r['paced'] else 'No', r['scan'], r['host'], r['bld'],
                    r['room'], r['owner'], r['state'], r['cov'], r['label']])
    return buf.getvalue().rstrip('\n')
