"""Small helpers ported from the original JS so string and address handling matches it exactly."""
import functools
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# JS String.prototype.trim() and regex \s use this set (Python's differs slightly: it treats
# \x1c-\x1f and \x85 as whitespace and not ﻿).
JS_WS = ' \t\n\v\f\r                 　﻿'
JS_WS_CLASS = '[' + re.escape(JS_WS) + ']'


def js_trim(s):
    return (s or '').strip(JS_WS)


def nh(h):
    """Normalise a header for alias matching: lowercase alphanumerics only."""
    return re.sub(r'[^a-z0-9]', '', str(h).lower())


_IPV4 = re.compile(r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})', re.ASCII)


def ipn(s):
    """IPv4 string to integer, or -1 when it is not a dotted quad."""
    m = _IPV4.fullmatch(js_trim(s))
    if not m:
        return -1
    a, b, c, d = (int(x) for x in m.groups())
    if a > 255 or b > 255 or c > 255 or d > 255:
        return -1
    return ((a * 256 + b) * 256 + c) * 256 + d


def _js_number(s):
    if s is None:
        return math.nan
    s = js_trim(s)
    if s == '':
        return 0
    try:
        return float(s)
    except ValueError:
        return math.nan


def cidr_range(s):
    """'a.b.c.d/len' to (lo, hi) integers, or None."""
    p = s.split('/')
    b = ipn(p[0])
    l = _js_number(p[1] if len(p) > 1 else None)
    if b < 0 or not (0 <= l <= 32):
        return None
    sz = 2 ** (32 - l)
    lo = math.floor(b / sz) * sz
    if float(l).is_integer():
        sz, lo = int(sz), int(lo)
    return lo, lo + sz - 1


def norm_mac(m):
    x = re.sub(r'[^0-9a-f]', '', str(m or '').lower())
    return x if len(x) == 12 else ''


def to_ms(s):
    """ISO 8601 timestamp to epoch milliseconds (0 when it cannot be parsed), like Date.parse."""
    if not s:
        return 0
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH) // timedelta(milliseconds=1)


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def month_of(ms):
    if not ms:
        return ''
    d = EPOCH + timedelta(milliseconds=ms)
    return '%d-%02d' % (d.year, d.month)


def iso_day(ms):
    return (EPOCH + timedelta(milliseconds=ms)).strftime('%Y-%m-%d') if ms else ''


# Natural, case-insensitive ordering that approximates JS localeCompare(a, b, undefined,
# {numeric: true}) under the ICU root collation: whitespace < punctuation < symbols < digits <
# letters, digit runs compared by value, and lowercase before uppercase only as a tie-breaker.
def _char_class(ch):
    cat = unicodedata.category(ch)
    if cat[0] == 'Z' or ch in '\t\n\r\v\f':
        return 0
    if cat[0] == 'P':
        return 1
    if cat[0] in 'SC':
        return 2
    return 4


_CHUNK = re.compile(r'\d+|\D', re.ASCII)


@functools.lru_cache(maxsize=131072)
def natural_key(s):
    s = s or ''
    primary = []
    for tok in _CHUNK.findall(s):
        if tok[0].isdigit() and tok.isascii():
            primary.append((3, int(tok), ''))
        else:
            base = unicodedata.normalize('NFD', tok.casefold())
            primary.append((_char_class(tok), 0, base[:1] if base else tok))
    return tuple(primary), tuple(0 if c.islower() else 1 for c in s)


def natural_cmp(a, b):
    ka, kb = natural_key(a), natural_key(b)
    return (ka > kb) - (ka < kb)
