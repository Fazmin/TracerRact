from ip_explorer import ingest
from ip_explorer.merge import merge


def _bc(ip, mac=''):
    return {**{k: '' for k in ingest.BCF}, 'ip': ip, 'mac': mac}


def test_assets_without_an_address_show_another_ip():
    """First scope IP, then extra IP, then the BlueCat address of a MAC match; IPv4 before IPv6."""
    tb = ingest.parse_csv('address,addresses_scope,addresses_extra,macs\n'
                          ',fd00::1 10.0.0.5,10.0.0.9,\n'
                          ',,fd00::2 10.0.0.9,\n'
                          ',,fd00::3,\n'
                          ',,,aa:bb:cc:dd:ee:ff\n'
                          ',,,\n'
                          '10.0.0.1,10.0.0.1 10.0.0.2,,\n'
                          '300.1.1.1,10.0.0.3,,\n')
    recs, stats = merge([_bc('10.0.0.50', 'AA-BB-CC-DD-EE-FF')], tb, ingest.auto_map(tb['headers']))
    assert [(r['ip'], r['ipSrc'], r['blk']) for r in recs if r['hasRz']] == [
        ('10.0.0.5', 'Scope IP', 'Outside BlueCat ranges'),
        ('10.0.0.9', 'Extra IP', 'Outside BlueCat ranges'),
        ('fd00::3', 'Extra IP', 'No IP'),
        ('10.0.0.50', 'BlueCat address', 'Outside BlueCat ranges'),
        ('', '', 'No IP'),
        ('10.0.0.1', '', 'Outside BlueCat ranges'),
        ('300.1.1.1', '', 'No IP'),  # an invalid address is kept as runZero gave it
    ]
    # The stand-in does not count as an address in the file: no duplicate for 10.0.0.9, five rows without one.
    assert stats['noIp'] == 5 and stats['dupIp'] == 0 and stats['badIp'] == 1
