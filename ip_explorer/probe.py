"""Is an address alive? fping (ICMP echo) first; addresses that do not answer then get a TCP connect to a
few common ports, and a connection or a refusal (RST) both count as alive, much like nmap -sn. Without
fping (local development) only the TCP check is used.
"""
import functools
import os
import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

# Set TRACKER_TCP_PORTS to an empty value to use ping only.
TCP_PORTS = [int(p) for p in os.environ.get('TRACKER_TCP_PORTS', '80,443,22,445,3389').split(',') if p.strip()]
TCP_TIMEOUT = 1.0
TCP_WORKERS = 128
# Reserved for documentation (RFC 5737): nothing should ever answer for it.
NEVER_ALIVE = '192.0.2.1'

_ALIVE = re.compile(r'^(\S+) is alive(?: \(([\d.]+) ms\))?', re.M)
_VERSION = re.compile(r'[Vv]ersion\s+(\S+)')


class ProbeError(Exception):
    pass


def fping_path():
    return shutil.which('fping')


@functools.lru_cache(maxsize=1)
def fping_version():
    """'fping 5.1', or None when fping is not installed."""
    exe = fping_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, '-v'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 'fping'
    m = _VERSION.search(out.stdout + out.stderr)
    return 'fping ' + m.group(1) if m else 'fping'


def scanner(tcp=True):
    """What check() uses, in words for the UI."""
    ports = 'TCP ' + '/'.join(str(p) for p in TCP_PORTS)
    v = fping_version()
    if not v:
        return ports + ' only (fping is not installed)'
    return f'{v} + {ports}' if tcp and TCP_PORTS else f'{v} (TCP check off)'


def parse_fping(out):
    """fping -e output to {ip: rtt_ms} for the addresses that answered."""
    return {m.group(1): float(m.group(2)) if m.group(2) else None for m in _ALIVE.finditer(out)}


def ping(ips):
    """{ip: rtt_ms} for the addresses that answered an ICMP echo. Targets go in on stdin."""
    exe = fping_path()
    if not exe or not ips:
        return {}
    try:
        p = subprocess.run([exe, '-4', '-e', '-r', '1', '-t', '1000'], input='\n'.join(ips), capture_output=True,
                           text=True, timeout=60 + len(ips) * 0.05)
    except (OSError, subprocess.SubprocessError) as e:
        raise ProbeError(f'fping failed: {e}') from e
    # 0: all answered, 1: some did not, 2: an address could not be resolved. More is a real error.
    if p.returncode > 2:
        raise ProbeError(f'fping failed: {p.stderr.strip() or "exit code " + str(p.returncode)}')
    return parse_fping(p.stdout)


def _tcp_one(ip, ports):
    for port in ports:
        t0 = time.monotonic()
        try:
            with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
                return f'tcp/{port}', (time.monotonic() - t0) * 1000
        except ConnectionRefusedError:
            return f'tcp/{port} refused', (time.monotonic() - t0) * 1000
        except OSError:
            continue
    return None


def tcp(ips, ports=None):
    """{ip: (via, rtt_ms)} for the addresses that accepted or refused a connection on one of the ports."""
    ports = TCP_PORTS if ports is None else ports
    if not ips or not ports:
        return {}
    with ThreadPoolExecutor(min(TCP_WORKERS, len(ips))) as ex:
        res = dict(zip(ips, ex.map(lambda ip: _tcp_one(ip, ports), ips)))
    return {ip: r for ip, r in res.items() if r}


def check(ips, tcp_fallback=True):
    """{ip: (up, rtt_ms, via)} for every address in ips."""
    has_fping = bool(fping_path())
    if not has_fping and not (tcp_fallback and TCP_PORTS):
        raise ProbeError('No usable check: fping is not installed and the TCP check is off.')
    out = {ip: (True, rtt, 'icmp') for ip, rtt in ping(ips).items()}
    rest = [ip for ip in ips if ip not in out]
    if tcp_fallback or not has_fping:
        for ip, (via, rtt) in tcp(rest).items():
            out[ip] = (True, rtt, via)
    return {ip: out.get(ip, (False, None, '')) for ip in ips}


def self_test():
    """Whether anything answers for an address that cannot be in use. A transparent proxy, a firewall or
    Docker Desktop's user-space network can answer for every address, which would make all of them look
    alive. {'icmp': answered (None without fping), 'tcp': answered}."""
    return {'icmp': NEVER_ALIVE in ping([NEVER_ALIVE]) if fping_path() else None,
            'tcp': bool(tcp([NEVER_ALIVE]))}
