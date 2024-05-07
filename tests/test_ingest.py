from ip_explorer import ingest


def test_parse_csv_strips_bom_and_trims_headers():
    tb = ingest.parse_csv('﻿ IP Address ,Room\n130.113.1.1,241\n\n130.113.1.2,ITB 101\n')
    assert tb == {'headers': ['IP Address', 'Room'], 'rows': [['130.113.1.1', '241'], ['130.113.1.2', 'ITB 101']]}


def test_parse_csv_sniffs_semicolon_and_tab():
    assert ingest.parse_csv('a;b;c\n1;2;3')['rows'] == [['1', '2', '3']]
    assert ingest.parse_csv('a\tb\n1,5\t2')['rows'] == [['1,5', '2']]


def test_parse_csv_quoted_fields():
    tb = ingest.parse_csv('host,notes\r\n"a, b","line 1\nline ""2"""\r\n')
    assert tb['rows'] == [['a, b', 'line 1\nline "2"']]


def test_decode_falls_back_to_cp1252():
    assert ingest.decode('café'.encode('cp1252')) == 'café'
    assert ingest.decode('﻿café'.encode('utf-8')) == 'café'


def test_auto_map_and_ip_guess():
    tb = ingest.parse_csv('Device Address,Hostname,Weird\n10.0.0.1,a,x\n10.0.0.2,b,y\n')
    m = ingest.auto_map(tb['headers'])
    assert m['host'] == 1 and m['ip'] == -1
    assert ingest.guess_ip_col(tb) == 0


def test_ip_stats():
    tb = ingest.parse_csv('ip\n10.0.0.1\n10.0.0.1\nnot-an-ip\n\n" "\n')
    assert ingest.ip_stats(tb, 0) == {'rows': 4, 'withIp': 3, 'noIp': 1, 'valid': 2, 'dup': 1}


def test_bluecat_requires_ip_column():
    assert 'error' in ingest.bluecat_from_table(ingest.parse_csv('Host Name,Room\na,1\n'))


def test_bluecat_cleans_hosts_and_drops_bad_ips():
    tb = ingest.parse_csv('IP Address,Host Name,Subnet Name\n'
                          '130.113.1.5,"x.mcmaster.ca [ external ] , x.mcmaster.ca [ internal ]",ITB-Lab\n'
                          'n/a,y,ITB-Lab\n')
    objs = ingest.bluecat_from_table(tb)['objs']
    assert len(objs) == 1
    assert objs[0]['host'] == 'x.mcmaster.ca' and objs[0]['subName'] == 'ITB-Lab' and objs[0]['room'] == ''
