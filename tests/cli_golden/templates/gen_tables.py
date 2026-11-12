#!/usr/bin/env python3
"""Regenerate the Unicode and HTML entity tables at the end of cli/templates.neo.

The template compiler's reference behavior comes from Python's re, str and
html modules, so the tables are taken from the Python that produces the
goldens (the test box's 3.12, Unicode 15.0). Run it from the repository root:

    python3 tests/cli_golden/templates/gen_tables.py

It rewrites the region between the BEGIN/END GENERATED TABLES markers.
"""
from html.entities import html5
from pathlib import Path
import re
import re._casefix as casefix
import unicodedata
import _sre

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT/'cli/templates.neo'
BEGIN = '; ---- BEGIN GENERATED TABLES'
END = '; ---- END GENERATED TABLES'


def u24(value):
    return bytes((value & 255, (value >> 8) & 255, value >> 16))


def runs(predicate):
    found = []
    for code in range(0x110000):
        if predicate(code):
            if found and found[-1][1] == code-1:
                found[-1][1] = code
            else:
                found.append([code, code])
    return b''.join(u24(a)+u24(b) for a, b in found)


def neo_bytes(data):
    out = []
    for b in data:
        if 32 <= b < 127 and b not in (34, 92):
            out.append(chr(b))
        else:
            out.append('\\x%02x' % b)
    return '"'+''.join(out)+'"'


def case_table():
    def klass(code):
        return {code} | set(casefix._EXTRA_CASES.get(code, ()))
    records = []
    for code in range(0x110000):
        lower = _sre.unicode_tolower(code)
        fold = min(klass(lower))
        if lower != code or fold != code:
            records.append(u24(code)+u24(lower)+u24(fold))
    return b''.join(records)


def entity_table():
    records = []
    for name in sorted(html5, key=lambda key: key.encode()):
        key = name.encode()
        value = html5[name].encode()
        assert len(key) < 256 and len(value) < 256
        records.append(bytes((len(key),))+key+bytes((len(value),))+value)
    return b''.join(records)


def main():
    word = re.compile(r'\w')
    tables = [
        ('tpl_word_table', 'Python re \\w for str patterns: str.isalnum() and "_".',
         runs(lambda c: word.match(chr(c)) is not None)),
        ('tpl_digit_table', 'Python re \\d for str patterns: str.isdecimal().',
         runs(lambda c: chr(c).isdecimal())),
        ('tpl_unprintable_table', 'Code points for which str.isprintable() is false (repr escapes them).',
         runs(lambda c: not chr(c).isprintable())),
        ('tpl_case_table', 'code, simple lowercase (str.lower without special casing), re.IGNORECASE fold; 3 bytes each.',
         case_table()),
        ('tpl_entity_table', 'html.entities.html5 sorted by name: name length, name, value length, UTF-8 value.',
         entity_table()),
    ]
    lines = [BEGIN+' (tests/cli_golden/templates/gen_tables.py, Python '
             + unicodedata.unidata_version+' Unicode data); do not edit by hand.']
    for name, comment, data in tables:
        lines.append('; '+comment)
        lines.append('private fn '+name+'() -> Arr u8 _ = '+neo_bytes(data))
    lines.append(END)
    source = TARGET.read_text()
    start = source.index(BEGIN)
    stop = source.index(END)+len(END)
    TARGET.write_text(source[:start]+'\n'.join(lines)+source[stop:])
    print('wrote', TARGET, sum(len(data) for _, _, data in tables), 'table bytes')


if __name__ == '__main__':
    main()
