"""Runs the IP tracker's checks in the background for as long as the app runs.

Every gunicorn worker starts this loop (docker/gunicorn.conf.py). A lease in tracker_state lets one
process check at a time; if it stops, another takes over once the lease runs out.
"""
import logging
import os
import random
import socket
import threading
import time

import click
from flask import current_app
from flask.cli import with_appcontext

from . import probe, tracker
from .db import connect, transaction

TICK = 30               # seconds between looks for due checks
LEASE = 180             # how long the role is held without renewing; a chunk of checks takes well under this
SELF_TEST_EVERY = tracker.DAY
PRUNE_EVERY = tracker.DAY
ERROR_PAUSE = 600       # after a failed cycle, so a broken scanner does not fill the log

log = logging.getLogger(__name__)
_started = False
_start_lock = threading.Lock()


def _owner():
    return f'{socket.gethostname()}:{os.getpid()}'


def take_lease(db, owner, now):
    """Hold or renew the scheduler role. True while this owner has it."""
    with transaction(db):
        cur = tracker.get_state(db, 'lease')
        if cur and cur['owner'] != owner and cur['until'] > now:
            return False
        tracker.set_state(db, 'lease', {'owner': owner, 'until': now + LEASE, 'at': now})
    return True


def self_test(db):
    try:
        res = probe.self_test()
    except probe.ProbeError as e:
        res = {'error': str(e)}
    res['at'] = int(time.time())
    tracker.set_state(db, 'selftest', res)
    if res.get('tcp'):
        log.warning('IP tracker: TCP connections to %s (which cannot be in use) succeed, so the TCP check is off.',
                    probe.NEVER_ALIVE)
    if res.get('icmp'):
        log.warning('IP tracker: %s (which cannot be in use) answers ping, so results are unreliable.', probe.NEVER_ALIVE)
    return res


def cycle(db, owner):
    """Check everything that is due and keep the history trimmed."""
    t0 = time.time()
    try:
        res = tracker.run_due(db, tracker.prober(db), renew=lambda: take_lease(db, owner, int(time.time())))
    except probe.ProbeError as e:
        tracker.set_state(db, 'cycle', {'at': int(time.time()), 'error': str(e)})
        raise
    if res['checked']:
        res.update(at=int(time.time()), seconds=round(time.time() - t0, 1))
        tracker.set_state(db, 'cycle', res)
        log.info('IP tracker: checked %d addresses in %.1f s, %d answered.', res['checked'], res['seconds'], res['up'])
    pruned = tracker.get_state(db, 'pruned') or 0
    if t0 - pruned >= PRUNE_EVERY:
        tracker.prune(db)
        tracker.set_state(db, 'pruned', int(t0))
    return res


def gap():
    """The random pause before the next paced check."""
    return random.uniform(*tracker.PACE_GAP)


def run(path, once=False):
    """The loop. Every TICK, if this process holds the lease, it runs the self-test when that is due
    and checks whatever is due that is not paced. Between ticks it checks due paced addresses one at a
    time, a random gap() apart. With once, it does one tick and the paced addresses due then."""
    owner = _owner()
    leader, tested, next_tick = False, 0, 0
    while True:
        paced = False
        try:
            db = connect(path)
            try:
                if time.time() >= next_tick:
                    now = int(time.time())
                    next_tick = now + TICK
                    if take_lease(db, owner, now):
                        if not leader:
                            log.info('IP tracker: checks run in this process (%s).', owner)
                            tested = 0
                        leader = True
                        if now - tested >= SELF_TEST_EVERY:
                            self_test(db)
                            tested = now
                        cycle(db, owner)
                    else:
                        leader = False
                paced = leader and tracker.run_paced(db, tracker.prober(db)) is not None
            finally:
                db.close()
            pause = gap() if paced else max(1, next_tick - time.time())
        except Exception:
            log.exception('IP tracker: check cycle failed; trying again in %d minutes.', ERROR_PAUSE // 60)
            pause = ERROR_PAUSE
        if once and not paced:
            return
        time.sleep(pause)


def start(app):
    """Run the loop in a daemon thread, once per process. TRACKER=off turns it off."""
    global _started
    if not app.config.get('TRACKER'):
        return
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=run, args=(app.config['DATABASE'],), name='ip-tracker', daemon=True).start()


def release(app):
    """Give up the lease if this process holds it, so the next start (a recreated container has another
    host name) does not wait for it to run out."""
    if not _started:
        return
    db = connect(app.config['DATABASE'])
    try:
        with transaction(db):
            cur = tracker.get_state(db, 'lease')
            if cur and cur['owner'] == _owner():
                db.execute("DELETE FROM tracker_state WHERE key = 'lease'")
    finally:
        db.close()


@click.command('tracker')
@click.option('--once', is_flag=True, help='Check what is due once and exit.')
@with_appcontext
def tracker_command(once):
    """Run the IP tracker's checks in the foreground (gunicorn runs them itself in Docker)."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    click.echo(f'Scanner: {probe.scanner()}. Press Ctrl+C to stop.' if not once else f'Scanner: {probe.scanner()}.')
    run(current_app.config['DATABASE'], once=once)
