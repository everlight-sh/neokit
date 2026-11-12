#!/usr/bin/env python3
"""Regenerate the expected values in tests/cli_build_test.neo from Python.

The reference is compiler/build.py run by the test box's Python 3.12, with
Brotli hidden as the native build has no Brotli encoder. Run it on the test
box from anywhere, then run the tests:

    python3 tests/cli_golden/build/generate.py
    $NEO_HOME/neo test . --unit-only

It rewrites the region between the BEGIN/END GENERATED CASES markers with
build_cases(): records "op\\x1fa\\x1fb\\x1fc\\x1fexpected" separated by \\x1e.
It also rewrites gen_mime_table() in cli/build.neo: mimetypes.types_map, the
strict table content_type falls back to, as "\\x1e.ext\\x1ftype" records
(lowercase suffixes only, as guess_type lowercases the suffix it looks up).
tests/cli_build_test.neo's apply() maps each op to the gen_ function and
prints its answer the way this script prints Python's (list items are
separated by \\x1d, nested ones by \\x1c, errors are "error: TEXT").
"""
import mimetypes
import sys
import tomllib
from pathlib import Path

sys.modules['brotli'] = None  # import brotli raises ImportError
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from compiler.build import (BuildError, allowed_method, content_type, cors_function, cors_options,  # noqa: E402
                            embedded_response, header, read_frames, realtime_options, redirect_response,
                            redirect_rules, snapshot_file)

TARGET = ROOT/'tests/cli_build_test.neo'
BEGIN = '; ---- BEGIN GENERATED CASES'
END = '; ---- END GENERATED CASES'
BUILD = ROOT/'cli/build.neo'
MIME_BEGIN = '; ---- BEGIN GENERATED MIME TABLE'
MIME_END = '; ---- END GENERATED MIME TABLE'

URLS = ['/a.js', '/A.JS', '/x.css', '/index.html', '/f.txt', '/d.json', '/m.map', '/site.webmanifest', '/feed.xml', '/i.svg',
        '/p.png', '/p.jpg', '/p.jpeg', '/g.gif', '/w.webp', '/v.avif', '/f.ico', '/f.woff2', '/f.woff', '/a.wasm', '/d.pdf',
        '/data.csv', '/notes.md', '/a.tar.gz', '/a.tgz', '/x.svgz', '/x.css.gz', '/x.CSS.GZ', '/x.Z', '/x.bz2', '/x.xz', '/x.br',
        '/plain', '/.hidden', '/dir.d/file', '/a.htm', '/a.mp4', '/a.mp3', '/a.ttf', '/a.otf', '/a.zip', '/a.yaml', '/a.yml',
        '/a.rtf', '/a.bmp', '/a.tiff', '/a.mjs.gz', '/a.qqq', '/a.ogg', '/a.webm', '/a.wav', '/a.flac', '/a.ics', '/a.vtt',
        '/a.srt', '/a.toml', '/a.sh', '/a.py', '/a.c', '/a.tbz2', '/a.txz', '/a.taz', '/a.tz', '/a.7z', '/a.rar', '/a.doc',
        '/a.docx', '/a.xlsx', '/a.pptx', '/a.epub', '/a.odt', '/a.jsonld', '/a.m4a', '/a.aac', '/a.opus', '/a.mov', '/a.avi',
        '/a.mkv', '/a.heic', '/a.heif', '/a.jxl', '/a.eot', '/a.atom', '/a.rss', '/a.xhtml', '/a.mpg', '/a.mpeg', '/a.oga',
        '/a.ogv', '/a.jpe', '/a.ps', '/a.eps', '/a.tex', '/a.gz', '/a.tar', '/a.bz', '/x.svg.gz', '/x.SVGZ', '/a.b/c.CSV',
        '/a.json.gz', '/a.wasm.br', '/x.tar.Z', '/.tar.gz', '/a.', '/a.webp.gz', '/café.html', '/a.HTML', '/a.Mp4']
SNAPSHOTS = ['/', '/docs', '/docs/a/b', '/feed.xml', '/.well-known/security.txt', '/a.b/c', '/v1.2', '//x', '/café.txt']
FRAMES = [(b'', 0), (b'3\nabc', 1), (b'3\nabc0\n', 2), (b' 2 \nxy', 1), (b'+1\nz', 1), (b'3\nabc', 2), (b'x\nabc', 1),
          (b'3\nab', 1), (b'0\n0\n', 2), (b'5\na\nb\nc\n', 1), (b'2\n\xff\xfe', 1), (b'\t1\r\nq', 1)]
REDIRECTS = ['', 'redirects=1', '[[redirects]]\nfrom="/a"\nto="/b"', '[[redirects]]\nfrom="/a"\nto="/b?c=1"\nstatus=302',
             '[[redirects]]\nfrom="/a"\nto="https://x.test:8080/p?q"\nquery=false\nstatus=301',
             '[[redirects]]\nfrom="/a"\nto="https://x.test"', '[[redirects]]\nfrom="/a"\nto="https://x.test:123456"',
             '[[redirects]]\nfrom="/a"\nto="https://x.test:١٢"', '[[redirects]]\nfrom="/a"\nto="https://x.test:"',
             '[[redirects]]\nfrom="/a"\nto="https://x_y.test"', '[[redirects]]\nfrom="/a"\nto="https://x.test/"',
             '[[redirects]]\nfrom="/a"\nto="//x"', '[[redirects]]\nfrom="/a"\nto="/"', '[[redirects]]\nfrom="/a"\nto="/a b"',
             '[[redirects]]\nfrom="/a"\nto="/a\\\\b"', '[[redirects]]\nfrom="/a"\nto="/a\\"b"', '[[redirects]]\nfrom="/a/"\nto="/"',
             '[[redirects]]\nfrom="/[a]"\nto="/"', '[[redirects]]\nto="/"', '[[redirects]]\nfrom=1\nto="/"',
             '[[redirects]]\nfrom="/a"\nto=["/"]', '[[redirects]]\nfrom="/a"\nto={x=1.5}', '[[redirects]]\nfrom="/a"\nto=true',
             '[[redirects]]\nfrom="/a"\nto="/"\nstatus=200', '[[redirects]]\nfrom="/a"\nto="/"\nstatus=true',
             '[[redirects]]\nfrom="/a"\nto="/"\nquery="no"', '[[redirects]]\nfrom="/a"\nto="/"\nx=1', 'redirects=[1]',
             '[[redirects]]\nfrom="/a"\nto="/"\n[[redirects]]\nfrom="/a"\nto="/b"',
             '[[redirects]]\nfrom="/café"\nto="/"', '[[redirects]]\nfrom="/café/"\nto="/"', '[[redirects]]\nfrom="/a"\nto="ftp://x"',
             '[[redirects]]\nfrom="/a"\nto="http://x.test/café"', '[[redirects]]\nfrom="/a"\nto="/x\\u0000"',
             '[[redirects]]\nfrom="/a"\nto="/b"\nstatus=303\nquery=true\n[[redirects]]\nfrom="/c"\nto="https://y.test/?a=b"\nstatus=307']
CORS = [('', False), ('cors="*"', True), ('cors="*"', False),
        ('cors=["https://a.test","http://b.test:80"]\ncors_headers=["x-a"]\ncors_credentials=true', False),
        ('cors="https://a.test"', False), ('cors=[]', False), ('cors=["*","https://a.test"]', False), ('cors=["*"]', False),
        ('cors=["https://a.test/"]', False), ('cors="*"\ncors_headers=["a b"]', False), ('cors="*"\ncors_headers="x"', False),
        ('cors="*"\ncors_headers=[]', False), ('cors="*"\ncors_credentials=true', False), ('cors=["*"]\ncors_credentials=true', False),
        ('cors=["https://a.test"]\ncors_credentials=1', False), ('cors="*"\n[input]\na="text"', False),
        ('cors="*"\ncsrf=false\n[input]\na="text"', False), ('cors="*"\ncsrf=0\n[input]\na="text"', False), ('cors=[1]', False),
        ('cors=["https://a.test:١"]', False), ('cors=["HTTPS://a.test"]', False)]
REALTIME = ['', 'realtime=1', '[realtime]\nchannels=0', '[realtime]\nhistory=1025', '[realtime]\nmax_event_bytes=true',
            '[realtime]\nchannels=64\nhistory=1024\nmax_event_bytes=8192', '[realtime]\nchannels=2\nhistory=3\nmax_event_bytes=100',
            '[realtime]\nchannels=1.0']
EMBEDDED = [(b'hello', 'text/plain; charset=utf-8', 'public, no-cache'),
            (b'\x00\xff"\\\n\r\t', 'application/octet-stream', 'public, max-age=31536000, immutable'),
            (b'', 'text/html; charset=utf-8', 'x'), (b'a'*511, 'text/css; charset=utf-8', 'public, no-cache'),
            ('café'.encode(), 'text/html; charset=utf-8', 'public, no-cache')]


def text(value):
    return value if isinstance(value, bytes) else value.encode()


def shown(call):
    try:
        return call()
    except (BuildError, ValueError) as error:
        return 'error: '+str(error)


def flag(value):
    return 'True' if value else 'False'


def neo_bytes(data):
    return '"'+''.join(chr(b) if 32 <= b < 127 and b not in (34, 92) else '\\x%02x' % b for b in data)+'"'


def rules(source):
    found = redirect_rules(tomllib.loads(source))
    return '\x1c'.join('\x1d'.join([rule['source'], rule['target'], str(rule['status']), flag(rule['query']), flag(rule['absolute']),
                                    redirect_response(rule)]) for rule in found)


def cors(source, streaming):
    found = cors_options(tomllib.loads(source), 'app/api/route.toml', streaming)
    if found is None:
        return 'None'
    return '\x1d'.join(['\x1c'.join(found['origins']), '\x1c'.join(found['headers']), flag(found['credentials']),
                        cors_function('Cors_x', 'h(request)', ['GET', 'POST'], found)])


def realtime(source):
    return '\x1d'.join(str(value) for value in realtime_options(tomllib.loads(source)))


def cases():
    out = []

    def case(op, expected, a='', b='', c=''):
        out.append(b'\x1f'.join([text(op), text(a), text(b), text(c), text(expected)]))
    for url in URLS:
        case('content_type', content_type(url), url)
    for path in SNAPSHOTS:
        case('snapshot', str(snapshot_file(Path('/out/snapshots'), path)), path)
    for data, count in FRAMES:
        case('frames', shown(lambda: b'\x1d'.join(read_frames(data, count))), data, str(count))
    for source in REDIRECTS:
        case('redirects', shown(lambda: rules(source)), source)
    for source, streaming in CORS:
        case('cors', shown(lambda: cors(source, streaming)), source, flag(streaming))
    for source in REALTIME:
        case('realtime', shown(lambda: realtime(source)), source)
    for data, ctype, control in EMBEDDED:
        case('embedded', embedded_response(data, ctype, control), data, ctype, control)
    case('allowed', allowed_method(['GET', 'HEAD', 'POST']), 'GET\x1dHEAD\x1dPOST')
    case('allowed', allowed_method(['DELETE']), 'DELETE')
    case('header', header('r', 'X-A', '"v"'), 'r', 'X-A', '"v"')
    for data in [b'', b'abc', b"it's", b'say "hi"', b'both \' and "', b'\x00\t\n\r\\\x7f\x80\xff', 'café'.encode()]:
        case('bytes_repr', repr(data), data)
    return b'\x1e'.join(out)


def rewrite(path, begin, end, lines):
    source = path.read_text()
    start = source.index(begin)
    stop = source.index(end)+len(end)
    path.write_text(source[:start]+'\n'.join(lines)+source[stop:])
    print('wrote', path)


def mime_table():
    mimetypes.init()
    return ''.join('\x1e'+ext+'\x1f'+kind for ext, kind in sorted(mimetypes.types_map.items()) if ext == ext.lower()).encode()


def main():
    lines = [BEGIN+' (tests/cli_golden/build/generate.py, Python '+sys.version.split()[0]+'); do not edit by hand.',
             '; The cases: "op\\x1fa\\x1fb\\x1fc\\x1fexpected" records separated by \\x1e.',
             'fn build_cases() -> Arr u8 _ = '+neo_bytes(cases()),
             END]
    rewrite(TARGET, BEGIN, END, lines)
    rewrite(BUILD, MIME_BEGIN, MIME_END,
            [MIME_BEGIN+' (tests/cli_golden/build/generate.py, Python '+sys.version.split()[0]+'); do not edit by hand.',
             '; mimetypes.types_map: "\\x1e.ext\\x1ftype" records.',
             'private fn gen_mime_table() -> Arr u8 _ = '+neo_bytes(mime_table()),
             MIME_END])


if __name__ == '__main__':
    main()
