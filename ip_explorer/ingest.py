"""Reading uploaded CSVs: delimiter sniffing, runZero column auto-mapping and BlueCat rows."""
import csv
import io

from .merge import clean_host
from .util import ipn, js_trim, nh

csv.field_size_limit(2**31 - 1)


def decode(data):
    """Bytes from an upload to text. UTF-8 (BOM optional), falling back to Windows-1252."""
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def parse_csv(text):
    """Returns {'headers': [...], 'rows': [[...], ...]} with blank lines skipped."""
    if text.startswith('﻿'):
        text = text[1:]
    nl = text.find('\n')
    first = text[:nl] if nl > 0 else text
    delim = ','
    if first.count('\t') > first.count(','):
        delim = '\t'
    elif first.count(';') > first.count(','):
        delim = ';'
    rows = [r for r in csv.reader(io.StringIO(text, newline=''), delimiter=delim)
            if len(r) > 1 or (r and r[0] != '')]
    headers = [js_trim(h) for h in rows.pop(0)] if rows else []
    return {'headers': headers, 'rows': rows}


# runZero fields the explorer understands, with the header aliases that map to each one.
RZF = [
    {'k': 'ip', 'l': 'IP address', 'a': ['address', 'ip', 'ipaddress', 'ipv4', 'ipv4address', 'primaryip'], 'req': 1},
    {'k': 'ips2', 'l': 'Other IPs', 'a': ['addressesextra', 'extraaddresses', 'otheraddresses', 'additionaladdresses']},
    {'k': 'host', 'l': 'Hostname', 'a': ['hostname', 'name', 'dnsname', 'fqdn']},
    {'k': 'hosts', 'l': 'All hostnames', 'a': ['hostnames', 'allhostnames']},
    {'k': 'mac', 'l': 'MAC', 'a': ['newestmac', 'mac', 'macaddress', 'primarymac']},
    {'k': 'macs', 'l': 'All MACs', 'a': ['macs', 'allmacs']},
    {'k': 'vendor', 'l': 'MAC vendor', 'a': ['newestmacvendor', 'macvendor', 'vendor']},
    {'k': 'type', 'l': 'Device type', 'a': ['type', 'devicetype', 'assettype']},
    {'k': 'cat', 'l': 'Category', 'a': ['category']},
    {'k': 'os', 'l': 'Operating system', 'a': ['os', 'operatingsystem']},
    {'k': 'osv', 'l': 'OS version', 'a': ['osversion']},
    {'k': 'hw', 'l': 'Hardware / model', 'a': ['hw', 'hardware', 'model']},
    {'k': 'risk', 'l': 'Risk', 'a': ['risk', 'risklevel']},
    {'k': 'alive', 'l': 'Alive', 'a': ['alive', 'isalive', 'online']},
    {'k': 'first', 'l': 'First seen', 'a': ['firstseeniso', 'firstseen']},
    {'k': 'last', 'l': 'Last seen', 'a': ['lastseeniso', 'lastseen', 'lastupdatediso']},
    {'k': 'tags', 'l': 'Tags', 'a': ['tags']},
    {'k': 'owners', 'l': 'Owners (runZero)', 'a': ['owners', 'owner']},
    {'k': 'tcp', 'l': 'TCP ports', 'a': ['serviceportstcp', 'tcpports', 'openports']},
    {'k': 'udp', 'l': 'UDP ports', 'a': ['serviceportsudp', 'udpports']},
    {'k': 'proto', 'l': 'Protocols', 'a': ['serviceprotocols', 'protocols']},
    {'k': 'prod', 'l': 'Service products', 'a': ['serviceproducts', 'products']},
    {'k': 'agent', 'l': 'Scanned by', 'a': ['lastagentname', 'agent', 'explorer']},
    {'k': 'det', 'l': 'Detected by', 'a': ['detectedby']},
    {'k': 'eol', 'l': 'OS end-of-life', 'a': ['eolos']},
    {'k': 'serial', 'l': 'Serial numbers', 'a': ['serialnumbers', 'serial']},
    {'k': 'vlan', 'l': 'VLAN', 'a': ['vlan']},
    {'k': 'func', 'l': 'Functions', 'a': ['functions']},
    {'k': 'crit', 'l': 'Criticality', 'a': ['criticality']},
    {'k': 'notes', 'l': 'Comments', 'a': ['comments', 'notes']},
]
RZF_KEYS = [f['k'] for f in RZF]


def auto_map(headers):
    idx = {}
    for i, h in enumerate(headers):
        idx.setdefault(nh(h), i)
    m = {}
    for f in RZF:
        m[f['k']] = next((idx[a] for a in f['a'] if a in idx), -1)
    return m


def guess_ip_col(tb):
    """The column whose first 60 values are most often IPv4, if that is more than half."""
    best, best_score = -1, 0
    tot = min(len(tb['rows']), 60)
    for c in range(len(tb['headers'])):
        ok = sum(1 for r in tb['rows'][:tot] if c < len(r) and ipn(r[c]) >= 0)
        if tot and ok / tot > best_score:
            best_score, best = ok / tot, c
    return best if best_score > .5 else -1


def column_samples(tb, limit=400):
    """First non-empty value of each column, for the mapping step."""
    out = []
    for c in range(len(tb['headers'])):
        out.append(next((r[c] for r in tb['rows'][:limit] if c < len(r) and r[c]), ''))
    return out


def ip_stats(tb, col):
    rows = tb['rows']
    with_ip = valid = dup = 0
    seen = set()
    if col >= 0:
        for r in rows:
            v = js_trim(r[col] if col < len(r) else '')
            if v:
                with_ip += 1
                if ipn(v) >= 0:
                    valid += 1
                if v in seen:
                    dup += 1
                seen.add(v)
    return {'rows': len(rows), 'withIp': with_ip, 'noIp': len(rows) - with_ip, 'valid': valid, 'dup': dup}


BCF = {'ip': ['ipaddress', 'ip', 'address'], 'host': ['hostname', 'hostnames', 'host'], 'an': ['addressname', 'name'],
       'mac': ['macaddress', 'mac'], 'loc': ['location'], 'owner': ['owner'], 'room': ['room'], 'state': ['state', 'status'],
       'parent': ['parentblock', 'block'], 'sub': ['subnet', 'network', 'cidr'], 'subName': ['subnetname', 'networkname']}


def bluecat_from_table(tb):
    """BlueCat export rows to address dicts. Returns {'objs': [...]} or {'error': ...}."""
    idx = {nh(h): i for i, h in enumerate(tb['headers'])}
    m = {k: next((idx[a] for a in aliases if a in idx), -1) for k, aliases in BCF.items()}
    if m['ip'] < 0:
        return {'error': 'No "IP Address" column found. This does not look like the BlueCat export.'}
    out = []
    for r in tb['rows']:
        def g(k):
            c = m[k]
            return js_trim(r[c]) if 0 <= c < len(r) else ''
        o = {k: g(k) for k in BCF}
        o['host'] = clean_host(o['host'])
        if ipn(o['ip']) >= 0:
            out.append(o)
    return {'objs': out, 'found': [k for k in m if m[k] >= 0], 'missing': [k for k in m if m[k] < 0]}
