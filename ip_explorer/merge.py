"""Merging runZero assets with BlueCat addresses, and inferring building and room.

Started as a line-by-line port of merge() from the original single-file explorer. Records are plain
dicts with the keys the original JS used, so the browser code can render them. Unlike the original,
every address runZero lists for an asset (address, addresses_scope, addresses_extra) is used to
match it to BlueCat, link further BlueCat addresses to it and place it in a subnet.
"""
import re

from .util import JS_WS_CLASS, cidr_range, ipn, js_trim, month_of, norm_mac, to_ms

LABEL = {'ITB': 'ITB · Computing & Software', 'JHE': 'JHE · John Hodgins Engineering', 'ABB': 'ABB · A.N. Bourns Science',
         'ETB': 'ETB · Engineering Technology', 'MARC': 'MARC · Automotive Research Centre', 'BSB': 'BSB · Burke Science',
         'GS': 'GS · General Science / Tandem', 'ADL': 'ADL · Applied Dynamics Lab', 'MIP': 'MIP · Innovation Park',
         'NRB': 'NRB · Nuclear Research', 'CRL': 'CRL · Communications Research Lab', 'REAC': 'REAC · Reactor', 'MDCL': 'MDCL',
         'AMC': 'AMC · MMRI / FlexLab', 'HATCH': 'Hatch Engineering Annex', 'McKay': 'McKay', 'Woodstock': 'Woodstock',
         'Mayfair': 'Mayfair', 'VPN': 'VPN range', 'Cloud': 'Cloud / OCI / Azure', 'Other': 'Other / unknown'}
ROOMTOK = {'ITB': 'ITB', 'ETB': 'ETB', 'JHE': 'JHE', 'JHEA': 'JHE', 'ABB': 'ABB', 'ABBC': 'ABB', 'BSB': 'BSB', 'MARC': 'MARC',
           'NRB': 'NRB', 'NRBA': 'NRB', 'GSB': 'GS', 'CRL': 'CRL', 'MIP': 'MIP', 'ADL': 'ADL', 'MDCL': 'MDCL', 'HATCH': 'HATCH',
           'MCKAY': 'McKay', 'WOODSTOCK': 'Woodstock', 'REACTOR': 'REAC'}
LOC2CODE = [('hodgins', 'JHE'), ('bourns', 'ABB'), ('automotive research', 'MARC'), ('burke science', 'BSB'),
            ('information technology building', 'ITB'), ('engineering technology', 'ETB'), ('general science', 'GS'),
            ('nuclear research', 'NRB'), ('communications research', 'CRL'), ('innovation park', 'MIP'),
            ('applied dynamics', 'ADL'), ('mckay', 'McKay'), ('woodstock', 'Woodstock')]
_SUB_CODES = ['ITB', 'JHE', 'ABB', 'MARC', 'BSB', 'ADL', 'MIP', 'NRB', 'CRL', 'REAC', 'MDCL', 'AMC']
_SUB_CODE_RE = [(c, re.compile('^' + c + r'\b', re.ASCII)) for c in _SUB_CODES]


def sub_building(name):
    """Building code from a BlueCat subnet name."""
    u = (name or '').upper()
    for code, rx in _SUB_CODE_RE:
        if rx.search(u) or u.startswith(code + '-'):
            return code
    if u.startswith('ETB'):
        return 'ETB'
    if re.match(r'GS\b', u, re.ASCII):
        return 'GS'
    if 'RHPCS' in u or 'ABB' in u:
        return 'ABB'
    if 'HATCH' in u:
        return 'HATCH'
    if u.startswith('MCKAY'):
        return 'McKay'
    if u.startswith('WOODSTOCK'):
        return 'Woodstock'
    if 'MAYFAIR' in u:
        return 'Mayfair'
    if u.startswith('VPN'):
        return 'VPN'
    if re.match(r'(OCI|AZURE|ASAV|SPARE|HHSC)', u):
        return 'Cloud'
    return 'Other'


_INT_EXT = re.compile(JS_WS_CLASS + r'*\[' + JS_WS_CLASS + r'*(internal|external)' + JS_WS_CLASS + r'*\]' + JS_WS_CLASS + '*')


def clean_host(h):
    """'a [ internal ] , a [ external ]' -> 'a'."""
    seen = []
    for p in str(h or '').split(' , '):
        p = js_trim(_INT_EXT.sub('', p))
        if p and p not in seen:
            seen.append(p)
    return ', '.join(seen)


_ROOM_SEP = re.compile(r'(?:' + JS_WS_CLASS + r'|[_\-/])+')


def norm_room(s):
    return js_trim(_ROOM_SEP.sub(' ', str(s or '').upper()))


def pretty_loc(s):
    return js_trim(str(s or '').replace('_', ' '))


_WS_SPLIT = re.compile(JS_WS_CLASS + '+')
_LIST_SPLIT = re.compile(r'(?:' + JS_WS_CLASS + r'|[,;])+')


def parse_tags(s):
    out = []
    for t in _WS_SPLIT.split(str(s or '')):
        if not t:
            continue
        i = t.find('=')
        out.append(['tag', t] if i < 0 else [t[:i].lower(), pretty_loc(t[i + 1:])])
    return out


def split_list(s):
    return [x for x in _LIST_SPLIT.split(str(s or '')) if x]


def _unique(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


_MATCH_STAT = {'IP': 'byIp', 'Scope IP': 'byScope', 'Extra IP': 'byExtra'}
B1, B2 = ipn('130.113.0.0'), ipn('172.16.0.0')


def blk_of(n):
    if n < 0:
        return 'No IP'
    if B1 <= n <= B1 + 65535:
        return '130.113.0.0/16'
    if B2 <= n <= B2 + 1048575:
        return '172.16.0.0/12'
    return 'Outside BlueCat ranges'


def blank():
    return {'id': 0, 'hasRz': False, 'hasBc': False, 'ip': '', 'n': -1, 'scopeIps': [], 'ips2': [], 'host': '', 'hosts': [],
            'mac': '', 'macs': [], 'vendor': '', 'bcIp': '', 'bcHost': '', 'an': '', 'owner': '', 'room': '', 'roomL': '',
            'bldC': '', 'bld': '', 'bldSrc': '', 'sub': '', 'subName': '', 'parent': '', 'state': '', 'loc': '', 'type': '',
            'cat': '', 'os': '', 'osv': '', 'hw': '', 'risk': '', 'alive': '', 'first': 0, 'last': 0, 'tcp': [], 'udp': [],
            'proto': [], 'prod': [], 'tags': [], 'agent': '', 'det': '', 'eol': '', 'serial': '', 'vlan': '', 'func': '',
            'crit': '', 'notes': '', 'cov': '', 'match': '', 'blk': '', 'bcMore': [], 'ipSrc': '', 'raw': None,
            'dup': False}


def addresses(r):
    """Every address of a record: runZero's primary, scope and extra IPs, then the BlueCat addresses it was matched to."""
    return _unique([x for x in [r['ip'], *r['scopeIps'], *r['ips2'], r['bcIp'], *(b['ip'] for b in r['bcMore'])] if x])


def _stand_in_ip(r):
    """For a runZero asset with no address: its first other address (IPv4 preferred) and where that came from."""
    cands = ([(x, 'Scope IP') for x in r['scopeIps']] + [(x, 'Extra IP') for x in r['ips2']]
             + ([(r['bcIp'], 'BlueCat address')] if r['bcIp'] else []))
    return next((c for c in cands if ipn(c[0]) >= 0), cands[0] if cands else ('', ''))


def _linked(b, via):
    """A further BlueCat address of a runZero asset, as the detail panel lists it."""
    return {'ip': b['ip'], 'via': via, 'host': b['host'].split(', ')[0] or b['an'], 'sub': b['sub'],
            'subName': b['subName'], 'owner': b['owner'], 'room': b['room'], 'state': b['state']}


def _apply_bc(r, b):
    r['hasBc'] = True
    r['bcIp'] = b['ip']
    r['bcHost'] = b['host']
    r['an'] = b['an']
    r['state'] = b['state']
    r['loc'] = b['loc']
    if b['owner']:
        r['owner'] = b['owner']
    r['room'] = b['room']
    r['sub'] = b['sub']
    r['subName'] = b['subName']
    r['parent'] = b['parent']
    if b['mac'] and not r['mac']:
        r['mac'] = b['mac']
    if b['host']:
        for h in b['host'].split(', '):
            if h not in r['hosts']:
                r['hosts'].append(h)
    if b['an'] and b['an'] not in r['hosts'] and not r['hosts']:
        r['hosts'].append(b['an'])


def merge(bc_objs, rz_tb, col_map):
    """bc_objs: BlueCat dicts (see ingest.bluecat_from_table); rz_tb: parsed runZero table or None;
    col_map: runZero field -> column index. Returns (records, stats)."""
    stats = {'rzRows': 0, 'byIp': 0, 'byScope': 0, 'byExtra': 0, 'byMac': 0, 'bcLinked': 0, 'rzOnly': 0, 'bcOnly': 0,
             'noIp': 0, 'badIp': 0, 'dupIp': 0, 'noLoc': 0}
    subs, sub_list = set(), []
    for b in bc_objs:
        if b['sub'] and b['sub'] not in subs:
            rng = cidr_range(b['sub'])
            if rng:
                subs.add(b['sub'])
                sub_list.append({'lo': rng[0], 'hi': rng[1], 'cidr': b['sub'], 'name': b['subName'],
                                 'parent': b['parent'], 'size': rng[1] - rng[0]})
    sub_list.sort(key=lambda s: s['size'])

    def sub_for(n):
        return next((s for s in sub_list if s['lo'] <= n <= s['hi']), None) if n >= 0 else None

    bc_by_ip, bc_by_mac, used = {}, {}, set()
    for i, b in enumerate(bc_objs):
        bc_by_ip[b['ip']] = i
        m = norm_mac(b['mac'])
        if m:
            bc_by_mac.setdefault(m, []).append(i)

    recs, seen_ip = [], {}
    if rz_tb:
        headers = rz_tb['headers']
        stats['rzRows'] = len(rz_tb['rows'])
        for row in rz_tb['rows']:
            def g(k):
                c = col_map.get(k, -1)
                return js_trim(row[c]) if 0 <= c < len(row) else ''

            r = blank()
            r['hasRz'] = True
            r['ip'] = g('ip')
            r['n'] = ipn(r['ip'])
            if r['ip'] and r['n'] < 0:
                stats['badIp'] += 1
            if not r['ip']:
                stats['noIp'] += 1
            if r['ip'] and r['ip'] in seen_ip:
                stats['dupIp'] += 1
                r['dup'] = True
                seen_ip[r['ip']]['dup'] = True
            elif r['ip']:
                seen_ip[r['ip']] = r
            r['scopeIps'] = split_list(g('scopeIps'))
            r['ips2'] = split_list(g('ips2'))
            r['hosts'] = _unique(split_list(g('hosts')))
            r['host'] = g('host') or (r['hosts'][0] if r['hosts'] else '')
            r['mac'] = g('mac')
            r['macs'] = _unique(split_list(g('macs')) + ([r['mac']] if r['mac'] else []))
            r['vendor'] = g('vendor')
            r['type'], r['cat'], r['os'], r['osv'], r['hw'] = g('type'), g('cat'), g('os'), g('osv'), g('hw')
            r['risk'] = g('risk').lower()
            al = g('alive').lower()
            r['alive'] = 'Yes' if al in ('true', 'yes', '1') else ('No' if al in ('false', 'no', '0') else '')
            r['first'], r['last'] = to_ms(g('first')), to_ms(g('last'))
            r['tags'] = parse_tags(g('tags'))
            ow = g('owners')
            if ow:
                r['owner'] = ', '.join(x[x.find('=') + 1:] if '=' in x else x for x in _WS_SPLIT.split(ow))
            r['tcp'], r['udp'] = split_list(g('tcp')), split_list(g('udp'))
            r['proto'], r['prod'] = split_list(g('proto')), split_list(g('prod'))
            for k in ('agent', 'det', 'eol', 'serial', 'vlan', 'func', 'crit', 'notes'):
                r[k] = g(k)
            r['raw'] = {headers[c]: row[c] for c in range(len(headers)) if c < len(row) and row[c] != ''}

            # Every address runZero lists can match. The first BlueCat hit (primary, then scope, then
            # extra IPs) supplies room, owner and subnet; any further hits are linked to the same asset.
            b, how, more, seen = None, '', [], set()
            for via, ips in (('IP', [r['ip']]), ('Scope IP', r['scopeIps']), ('Extra IP', r['ips2'])):
                for x in ips:
                    i = bc_by_ip.get(x)
                    if i is None or x in seen:
                        continue
                    seen.add(x)
                    if b is None:
                        b, how = i, via
                    else:
                        more.append(_linked(bc_objs[i], via))
                        used.add(i)
            if b is not None:
                stats[_MATCH_STAT[how]] += 1
                stats['bcLinked'] += len(more)
            else:
                for mac in r['macs']:
                    hits = bc_by_mac.get(norm_mac(mac))
                    if hits and len(hits) == 1:
                        b, how = hits[0], 'MAC'
                        stats['byMac'] += 1
                        break
            if b is not None:
                used.add(b)
                _apply_bc(r, bc_objs[b])
                r['match'], r['cov'], r['bcMore'] = how, 'Both', more
            else:
                r['cov'] = 'runZero only'
                stats['rzOnly'] += 1
            if not r['ip']:
                # Shown, sorted and grouped by its stand-in; ipSrc keeps where it came from for the detail panel.
                r['ip'], r['ipSrc'] = _stand_in_ip(r)
                r['n'] = ipn(r['ip'])
            recs.append(r)

    for i, b in enumerate(bc_objs):
        if i in used:
            continue
        r = blank()
        r['hasBc'] = True
        r['ip'] = b['ip']
        r['n'] = ipn(b['ip'])
        r['mac'] = b['mac']
        _apply_bc(r, b)
        r['cov'] = 'BlueCat only'
        r['host'] = b['host'].split(', ')[0] or b['an']
        stats['bcOnly'] += 1
        recs.append(r)

    ref = max([r['last'] for r in recs] + [0])
    for i, r in enumerate(recs):
        r['id'] = i
        r['blk'] = blk_of(r['n'])
        if not r['sub']:
            # The first of the asset's addresses, primary IP first, that sits in a BlueCat subnet.
            s = next(filter(None, (sub_for(ipn(x)) for x in addresses(r))), None)
            if s:
                r['sub'], r['subName'], r['parent'] = s['cidr'], s['name'], s['parent']
        code, src = '', ''
        rm = norm_room(r['room'])
        mt = re.match(r'[A-Z]+', rm)
        if mt and mt.group(0) in ROOMTOK:
            code, src = ROOMTOK[mt.group(0)], 'room name'
        if not code and r['subName']:
            c2 = sub_building(r['subName'])
            if c2 != 'Other':
                code, src = c2, 'BlueCat subnet'
        loc = None
        for t in r['tags']:
            if t[0] == 'location':
                loc = t[1]
        if not code and loc:
            lw = loc.lower()
            for needle, c in LOC2CODE:
                if needle in lw:
                    code, src = c, 'runZero location tag'
                    break
            if not code:
                code, src = 'LOC:' + loc, 'runZero location tag'
        if not code:
            code, src = 'Other', 'unknown'
        is_loc = code.startswith('LOC:')
        r['bldC'] = code
        r['bld'] = code[4:] if is_loc else LABEL.get(code, code)
        r['bldSrc'] = src
        if r['room']:
            rn = rm[len(code) + 1:] if rm.startswith(code + ' ') else rm
            r['roomL'] = ('' if is_loc else code + ' · ') + rn
        if r['hasRz'] and not r['hasBc'] and not r['sub']:
            stats['noLoc'] += 1
        if not r['hasRz']:
            r['seenB'] = '(not scanned)'
        elif not r['last']:
            r['seenB'] = '(unknown)'
        else:
            d = (ref - r['last']) / 86400000
            r['seenB'] = ('Within 7 days' if d <= 7 else '8 to 30 days' if d <= 30
                          else '31 to 90 days' if d <= 90 else 'Over 90 days')
        r['seenM'] = month_of(r['last']) if r['last'] else ''
        r['seenD'] = (ref - r['last']) // 86400000 if r['last'] else -1
    stats['ref'] = ref
    return recs, stats
