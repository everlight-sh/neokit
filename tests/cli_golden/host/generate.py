#!/usr/bin/env python3
"""Regenerate the expected values in tests/cli_host_test.neo from Python.

cli/host.neo reproduces what compiler/*.py gets from pathlib, os.path,
fnmatch, os.walk, shutil, tempfile and subprocess, so the reference is the
Python that runs the compiler (the test box's 3.12). Run it from anywhere on
the test box, then run the tests:

    python3 tests/cli_golden/host/generate.py
    $NEO_HOME/neo test . --unit-only
    $NEO_HOME/neo test tests/cli_golden/host/host.neo.toml

It rewrites the region between the BEGIN/END GENERATED CASES markers:

- host_cases(): the pure cases, records separated by \\x1e, each
  "op\\x1fa\\x1fb\\x1fc\\x1fexpected" (lists inside a field are separated by
  \\x1d). tests/cli_host_test.neo's apply() maps each op to the host_
  function and prints its answer the way this script prints Python's.
- fixture_spec(): a scratch tree (files with modes, directories, relative
  symbolic links) that both this script and the fs_test_ functions build,
  and the expected walks, globs, listdir, resolve and copytree results on it,
  as paths relative to the tree.
"""
import fnmatch
import os
from pathlib import Path, PosixPath, PurePosixPath
import posixpath
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TARGET = ROOT/'tests/cli_host_test.neo'
BEGIN = '; ---- BEGIN GENERATED CASES'
END = '; ---- END GENERATED CASES'

PATHS = ['', '.', '..', '...', '/', '//', '///', '////a', '//a/b', '///a', 'a', 'a/', 'a//b/', './a/./b/.', 'a/b/..',
         '/..', '/../a', '../x', '../../x/..', '.bashrc', 'a/.bashrc', '.bashrc.d/x', 'a.tar.gz', 'x.', 'x..', '..x', 'a/.b.c',
         '.', 'a/..', '~', '~/x', '~user/x', 'café/ñ.txt', 'café', '/a/b/', '/a/b', '/a', '/tmp//x/./y/../z',
         'a/b/c.d/e', 'a b/c d.e f', 'archive.tar.gz/', 'name.', '.x.y.z', 'dir.d/file', '/.', '/./.', './..', 'a/./..']
PAIRS = [('', ''), ('', 'a'), ('a', ''), ('a', 'b'), ('a/', 'b'), ('a', '/b'), ('/a', 'b'), ('/', 'x'), ('//', 'x'), ('//a', 'b'),
         ('a', '//b'), ('.', 'x'), ('x', '.'), ('a', '..'), ('a/b', '../c'), ('a', 'b/'), ('a', './b/.'), ('/a/b', '/a'),
         ('/a/b', '/a/'), ('/a/b', '/'), ('/a', '/a'), ('/a/', '/a'), ('/a', '/a/b'), ('/ab', '/a'), ('/a-b/x', '/a'),
         ('/a/x', '/a-b'), ('a/../b', 'a'), ('a/b', '.'), ('a', ''), ('/a', '.'), ('//a', '/'), ('//a/b', '//a'),
         ('/a', '//a'), ('a/b/c', 'a/b'), ('a/b/c', 'a/c'), ('café/x', 'café'), ('/x/./y', '/x'), ('.', '.'), ('', '.'),
         ('a/b', 'a/b/'), ('/a/b', 'a/b'), ('/tmp/x', '/tmp/x/y')]
NAMES = ['x', 'b/c', '', '.', '..', 'x/', 'n.txt', '.hidden', 'café']
CWDS = ['/home/user/project', '/', '//net/share', '/a/b/../c']
HOMES = ['/root', '/home/user/', '/', '', '//', 'relative/home', '~weird']
EXPAND = ['~', '~/x', '~//x/./y', '~/', '~/..', 'a/~', '/~', '~nonexistentuser/x', 'x', '', '.']
SORT_SETS = [['a-b/x', 'a/x', 'a', 'a.b', 'a/x/y', 'a b', 'a\tb', 'b', 'A'],
             ['/a/b', '/a', '/a-b', '/a.b/c', '/a/b/c', '/a b', '/', '/ab'],
             ['x/café', 'x/cafe', 'x/caf', 'x/cafè/y', 'x/caf/z'],
             ['styles/site.css', 'styles/a-b.css', 'styles/a.css', 'styles/a/b.css', 'styles/a_b.css']]
PATTERNS = [('*.css', 'site.css'), ('*.css', '.hidden.css'), ('*.css', 'site.CSS'), ('*.css', 'a.css.map'), ('*.css', '.css'),
            ('*', ''), ('*', '.x'), ('neoc*', 'neoc'), ('neoc*', 'neoc-linux-x86_64'), ('neoc*', 'xneoc'), ('client-*.js', 'client-abc.js'),
            ('client-*.js', 'client.js'), ('LICENSE*', 'LICENSE'), ('LICENSE*', 'LICENSE.md'), ('LICENSE*', 'license'),
            ('?.txt', 'a.txt'), ('?.txt', 'ab.txt'), ('?.txt', 'é.txt'), ('[abc].txt', 'b.txt'), ('[abc].txt', 'd.txt'),
            ('[!abc].txt', 'd.txt'), ('[!abc].txt', 'a.txt'), ('[a-c]x', 'bx'), ('[a-c]x', 'dx'), ('[]]', ']'), ('[!]]', 'a'),
            ('[', '['), ('[a', '[a'), ('a[', 'a['), ('*a*b*', 'xaybz'), ('*a*b*', 'xbya'), ('a*', 'a'), ('**', 'abc'),
            ('*.*', 'noext'), ('page.toml', 'page.toml'), ('page.toml', 'Page.toml'), ('*x', 'xxx'), ('a?c', 'abc'),
            ('a?c', 'ac'), ('[-a]', '-'), ('[a-]', '-'), ('caf[é]', 'café'), ('.neo-build', '.neo-build'), ('__pycache__', '__pycache__')]
UTF8 = [b'', b'plain', 'café ✓ 😀'.encode(), b'\xff', b'abc\xff', b'\x80', b'\xc0\x80', b'\xc1\xbf', b'\xc2', b'ok\xc2',
        b'\xc2\x41', b'\xe2\x82', b'\xe2\x82\xac', b'\xe2\x82A', b'\xe2A\xac', b'\xe0\x80\x80', b'\xe0\xa0\x80', b'\xed\xa0\x80',
        b'\xed\x9f\xbf', b'\xf0\x90\x80', b'\xf0\x90\x80\x80', b'\xf0\x80\x80\x80', b'\xf4\x8f\xbf\xbf', b'\xf4\x90\x80\x80',
        b'\xf5\x80\x80\x80', b'\xf0\x90A', b'a\xf0\x90\x80b', b'\xe0', b'\xf0', b'\xf0\x90', b'x\xfe\xff']
ERRNOS = [1, 2, 3, 5, 9, 11, 12, 13, 16, 17, 18, 20, 21, 22, 24, 26, 27, 28, 30, 32, 36, 39, 40, 95, 122, 200]
ERROR_PATHS = ['/path/to/file', "it's", 'both \' and "', 'café\x01\n', '']
COMMANDS = [(['bun', 'build'], 1, 0), (['/x/y', "it's"], 2, 0), (['a'], 0, 9), (['a'], 0, 15), (['a'], 0, 6), (['a'], 0, 29),
            (['a'], 0, 34), (['a'], 0, 35), (['a'], 0, 64), (['a'], 0, 33), ([], 255, 0)]

# The scratch tree: (kind, path, mode or target, content).
TREE = [
    ('f', 'a/x', 0o644, 'ax'), ('f', 'a-b/x', 0o644, 'abx'), ('f', 'a.b', 0o644, 'ab'), ('f', 'a/sub/page.toml', 0o644, 'p'),
    ('f', 'page.toml', 0o644, 'p'), ('f', 'a-b/page.toml', 0o644, 'p'), ('f', 'café/page.toml', 0o644, 'p'),
    ('f', 'café/é.txt', 0o644, 'e'), ('f', '.hidden/page.toml', 0o644, 'p'), ('f', '.dotfile', 0o644, 'd'),
    ('f', 'node_modules/pkg/page.toml', 0o644, 'p'), ('f', '__pycache__/m.pyc', 0o644, 'c'), ('f', 'build/out', 0o644, 'o'),
    ('f', 'styles/site.css', 0o644, 's'), ('f', 'styles/.hidden.css', 0o644, 'h'), ('f', 'styles/b.css', 0o644, 'b'),
    ('f', 'styles/A.css', 0o644, 'A'), ('f', 'styles/x.CSS', 0o644, 'X'), ('f', 'styles/a.css.map', 0o644, 'm'),
    ('d', 'styles/dir.css', None, None), ('d', 'empty', None, None), ('f', 'tools/bin/tool', 0o755, '#!/bin/sh\n'),
    ('f', 'other/bin', 0o644, 'not a directory'), ('f', 'a/sub/deep/page.toml', 0o644, 'p'),
    ('l', 'linkdir', 'a', None), ('l', 'linkfile', 'a.b', None), ('l', 'dangling', 'missing', None), ('l', 'a/up', '..', None),
    ('l', 'r1', 'a/sub', None), ('l', 'r2', 'r1', None), ('l', 'loop1', 'loop2', None), ('l', 'loop2', 'loop1', None),
    ('l', 'linkdir2/page.toml', '../page.toml', None),
    ('f', 'copyme/a.txt', 0o644, 'alpha'), ('f', 'copyme/run.sh', 0o755, '#!/bin/sh\necho run\n'), ('f', 'copyme/secret', 0o600, 's'),
    ('f', 'copyme/sub/b.txt', 0o644, 'beta'), ('f', 'copyme/skipme.txt', 0o644, 'x'), ('f', 'copyme/__pycache__/c.pyc', 0o644, 'c'),
    ('f', 'copyme/sub/skip-too/x', 0o644, 'x'), ('l', 'copyme/link.txt', 'a.txt', None), ('l', 'copyme/linkdir', 'sub', None),
    ('d', 'copyme/emptydir', None, None),
    ('f', 'keep/file', 0o644, 'kept'), ('l', 'victim/outside', '../keep', None), ('f', 'victim/inner/f', 0o644, 'f'),
]
WALK_SKIP_CACHE = ['node_modules', '.git', '__pycache__', '.neo-build', 'build', '.next', '.neokit']
WALK_SKIP_RELEASE = ['build', 'data', 'node_modules', 'vendor', '__pycache__', 'out']
RESOLVE = ['.', 'linkdir', 'linkdir/../a.b', 'linkdir/sub/page.toml', 'r2/page.toml', 'r2/../x', 'missing/x/../y', 'linkfile',
           'dangling', 'dangling/x', 'a/up/a/up', 'a/up/a/up/x/../x', 'a/./sub//page.toml/', 'loop1', 'loop1/x', 'a/sub/nope/../deep',
           'linkdir2/page.toml', 'café/é.txt', 'a/x/..', 'a/x/../sub']


def text(value):
    return value if isinstance(value, bytes) else value.encode()


def shown(call):
    try:
        return call()
    except (ValueError, RuntimeError, OSError) as error:
        return 'error: '+str(error)


def flag(value):
    return 'True' if value else 'False'


def neo_bytes(data):
    out = []
    for b in data:
        if 32 <= b < 127 and b not in (34, 92):
            out.append(chr(b))
        else:
            out.append('\\x%02x' % b)
    return '"'+''.join(out)+'"'


def pure_cases():
    cases = []

    def case(op, expected, a='', b='', c=''):
        fields = [text(op), text(a), text(b), text(c), text(expected)]
        for field in fields:
            assert b'\x1e' not in field and b'\x1f' not in field, (op, a, b, c)
        cases.append(b'\x1f'.join(fields))

    for p in PATHS:
        path = PurePosixPath(p)
        case('normalize', str(path), p)
        case('parts', repr(path.parts), p)
        case('parent', str(path.parent), p)
        case('parents', repr([str(x) for x in path.parents]), p)
        case('name', path.name, p)
        case('stem', path.stem, p)
        case('suffix', path.suffix, p)
        case('is_absolute', flag(path.is_absolute()), p)
        case('normpath', posixpath.normpath(p), p)
        for name in NAMES:
            case('with_name', shown(lambda: str(PosixPath(p).with_name(name))), p, name)
    for a, b in PAIRS + [(p, p) for p in PATHS]:
        case('join', str(PurePosixPath(a)/b), a, b)
        case('equal', flag(PurePosixPath(a) == PurePosixPath(b)), a, b)
        case('is_relative_to', flag(PurePosixPath(a).is_relative_to(b)), a, b)
        case('relative_to', shown(lambda: str(PurePosixPath(a).relative_to(b))), a, b)
        case('less', flag(PurePosixPath(a) < PurePosixPath(b)), a, b)
    real_getcwd = os.getcwd
    try:
        for cwd in CWDS:
            os.getcwd = lambda: cwd
            for p in PATHS + ['x/../../..', '../../../..']:
                case('abspath', posixpath.abspath(p), p, cwd)
            for a, b in PAIRS:
                case('relpath', shown(lambda: posixpath.relpath(a, b)), a, b, cwd)
    finally:
        os.getcwd = real_getcwd
    saved_home = os.environ.get('HOME')
    try:
        for home in HOMES:
            os.environ['HOME'] = home
            for p in EXPAND:
                case('expanduser', shown(lambda: str(Path(p).expanduser())), p, home)
    finally:
        if saved_home is None:
            del os.environ['HOME']
        else:
            os.environ['HOME'] = saved_home
    for items in SORT_SETS:
        normalized = [str(PurePosixPath(item)) for item in items]
        case('sorted', '\x1d'.join(str(p) for p in sorted(map(PurePosixPath, normalized))), '\x1d'.join(normalized))
    for pattern, name in PATTERNS:
        case('fnmatch', flag(fnmatch.fnmatchcase(name, pattern)), pattern, name)
    for data in UTF8:
        try:
            data.decode('utf-8')
            expected = ''
        except UnicodeDecodeError as error:
            expected = str(error)
        case('utf8', expected, data)
    for code in ERRNOS:
        case('strerror', os.strerror(code), str(code))
        for path in ERROR_PATHS:
            case('os_error', str(OSError(code, os.strerror(code), path)), str(code), path)
        case('os_error2', str(OSError(code, os.strerror(code), '/a', None, "/b'c")), str(code), '/a', "/b'c")
    for number in range(0, 66):
        try:
            expected = repr(signal.Signals(number))
        except ValueError:
            expected = ''
        case('signal', expected, str(number))
    for command, code, sig in COMMANDS:
        error = subprocess.CalledProcessError(-sig if sig else code, command)
        case('process_error', str(error), '\x1d'.join(command), str(128+sig if sig else code), str(sig))
    return b'\x1e'.join(cases)


def build_tree(root):
    for kind, path, extra, content in TREE:
        target = root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'd':
            target.mkdir(parents=True, exist_ok=True)
        elif kind == 'l':
            target.symlink_to(extra)
        else:
            target.write_text(content)
            target.chmod(extra)


def tree_spec():
    lines = []
    for kind, path, extra, content in TREE:
        if kind == 'd':
            lines.append('d\x1f'+path)
        elif kind == 'l':
            lines.append('l\x1f'+path+'\x1f'+extra)
        else:
            lines.append('f\x1f'+path+'\x1f'+str(extra)+'\x1f'+content)
    return '\x1e'.join(lines)


def rel(root, path):
    path = str(path)
    root = str(root)
    if path == root:
        return '.'
    assert path.startswith(root+'/'), (path, root)
    return path[len(root)+1:]


def walk(root, skip, hidden, skip_paths=()):
    out = []
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(f for f in folders if f not in skip and not (hidden and f.startswith('.'))
                            and Path(directory)/f not in skip_paths)
        out.append(rel(root, directory)+'|'+','.join(folders)+'|'+','.join(sorted(names))+'\n')
    return ''.join(out)


def listing(root):
    out = []
    for path in sorted(root.rglob('*')):
        kind = 'link' if path.is_symlink() else 'dir' if path.is_dir() else 'file'
        line = rel(root, path)+' '+kind
        if kind == 'file':
            line += ' %o %d' % (path.stat().st_mode & 0o7777, path.stat().st_size)
        out.append(line+'\n')
    return ''.join(out)


def effect_cases():
    expected = {}
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()/'TREE'
        build_tree(base)
        root = str(base)
        expected['expected_walk_cache'] = walk(root, WALK_SKIP_CACHE, False)
        expected['expected_walk_release'] = walk(root, WALK_SKIP_RELEASE, True)
        expected['expected_walk_fingerprint'] = walk(root, WALK_SKIP_CACHE, False, (base/'a/sub',))
        expected['expected_listdir'] = ','.join(sorted(os.listdir(root)))
        expected['expected_glob_css'] = ','.join(rel(root, p) for p in sorted((base/'styles').glob('*.css')))
        expected['expected_glob_bin'] = ','.join(rel(root, p) for p in sorted(base.glob('*/bin')))
        expected['expected_rglob_pages'] = ','.join(rel(root, p) for p in sorted(base.rglob('page.toml')))
        expected['expected_rglob_all'] = ','.join(rel(root, p) for p in sorted(base.rglob('*')))
        resolved = []
        for query in RESOLVE:
            try:
                answer = rel(root, (base/query).resolve())
            except RuntimeError as error:
                answer = 'error: '+str(error).replace(root, '<root>')
            resolved.append(query+' -> '+answer+'\n')
        expected['expected_resolve'] = ''.join(resolved)
        shutil.copytree(base/'copyme', base/'copied', ignore=shutil.ignore_patterns('skip*', '__pycache__'))
        expected['expected_copytree'] = listing(base/'copied')
        bad = base/'bad.txt'
        bad.write_bytes(b'ok\nabc\xff')
        expected['expected_read_bad'] = shown(lambda: bad.read_text())
    return expected


def main():
    lines = [BEGIN+' (tests/cli_golden/host/generate.py, Python '+sys.version.split()[0]+'); do not edit by hand.',
             '; The pure cases: "op\\x1fa\\x1fb\\x1fc\\x1fexpected" records separated by \\x1e.',
             'fn host_cases() -> Arr u8 _ = '+neo_bytes(pure_cases()),
             '; The scratch tree: "d PATH", "f PATH MODE CONTENT" and "l PATH TARGET" lines (\\x1f, \\x1e).',
             'fn fixture_spec() -> Arr u8 _ = '+neo_bytes(text(tree_spec())),
             'fn walk_skip_cache() -> Arr u8 _ = '+neo_bytes(text('\x1d'.join(WALK_SKIP_CACHE))),
             'fn walk_skip_release() -> Arr u8 _ = '+neo_bytes(text('\x1d'.join(WALK_SKIP_RELEASE))),
             'fn resolve_queries() -> Arr u8 _ = '+neo_bytes(text('\x1d'.join(RESOLVE))),
             '; Python\'s answers on the scratch tree, paths relative to it.']
    for name, value in effect_cases().items():
        lines.append('fn '+name+'() -> Arr u8 _ = '+neo_bytes(text(value)))
    lines.append(END)
    source = TARGET.read_text()
    start = source.index(BEGIN)
    stop = source.index(END)+len(END)
    TARGET.write_text(source[:start]+'\n'.join(lines)+source[stop:])
    print('wrote', TARGET)


if __name__ == '__main__':
    main()
