"""Regenerate the preview golden from compiler/cli.py's `neokit preview`
(Python 3.12's SimpleHTTPRequestHandler behind a ThreadingHTTPServer), the
reference.

Run on the test box (Python 3.12) from anywhere, then compare with the
native port (cli/preview.neo) through driver.neo:

    python3 tests/cli_golden/preview/generate.py
    NEO_HOME=/root/neo-dev/neo-feat $NEO_HOME/neo test tests/cli_golden/preview/preview.neo.toml

The script writes setup.tsv (the export to serve: files, directories and
symbolic links, some leading out of it), cases.tsv (the requests),
golden/fixture.out and preview.neo.toml. Both sides build the setup in a
scratch directory, run `neokit preview --dir SCRATCH/site --port PORT`,
send each request on a connection of its own and print the response, then
the lines the server logged for it, with the Date, Server and
Last-Modified values and the log times replaced; then two connections at
once (the first idle halfway through its request while the second is
served), the stop, and `neokit preview` errors.

The expected output is Python's, with the documented differences of the
native port applied:

- a file's Content-type is build.py's content_type() of its path, not
  SimpleHTTPRequestHandler.guess_type()'s;
- a case with an "as" target expects Python's exchange for that target
  (the log keeping the original request line): symbolic links leading out
  of the served directory are 404 (Python follows them), an index.html
  leading out is skipped for index.htm, and a NUL byte in the path is 404
  (Python's handler fails without a response);
- the native server is stopped with SIGTERM and exits with 0; Python is
  stopped with SIGINT, its Ctrl-C (SIGTERM kills it);
- port 70000 is "neokit: bind(): port must be 0-65535." with status 1
  (Python ends in an OverflowError traceback).

cases.tsv has one request per line: label, mode ("split": sent 3 bytes at
a time; "eof": the client shuts down its side after sending), the "as"
target or "", then the request as hex PREFIX, COUNT, REPEAT and SUFFIX
(PREFIX + COUNT * REPEAT + SUFFIX). setup.tsv: "file PATH HEX",
"dir PATH", "link PATH TARGET" or "abslink PATH TARGET" (TARGET below the
scratch directory, made absolute).
"""
import http.server
import os
from pathlib import Path
import queue
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from compiler import build  # noqa: E402

GOLDEN = HERE/'golden'

HARNESS = r'''
import sys
sys.path.insert(0, sys.argv[1])
import compiler.cli as cli
sys.argv = ['neokit', *sys.argv[2:]]
cli.main()
'''

# The export, and what lies beside it.
SETUP = [
    ('file', 'site/index.html', b'<!doctype html><title>Home</title><h1>Home</h1>\n'),
    ('file', 'site/about/index.html', b'<h1>About</h1>\n'),
    ('file', 'site/blog/post/index.html', b'<h1>Post</h1>\n'),
    ('file', 'site/legacy/index.htm', b'<h1>Legacy</h1>\n'),
    ('file', 'site/docs/readme.txt', b'read me\n'),
    ('file', 'site/docs/Zeta.md', b'# Zeta\n'),
    ('file', 'site/docs/alpha b.txt', b'alpha\n'),
    ('file', 'site/docs/é.txt', b'accent\n'),
    ('file', 'site/docs/q&a <x>.txt', b'qa\n'),
    ('file', 'site/docs/sub/note.txt', b'note\n'),
    ('file', 'site/assets/site.css', b'body{color:red}\n'),
    ('file', 'site/assets/app.js', b'console.log(1)\n'),
    ('file', 'site/assets/data.json', b'{"a":1}\n'),
    ('file', 'site/assets/logo.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>\n'),
    ('file', 'site/assets/font.woff2', b'wOF2\x00\x01'),
    ('file', 'site/assets/archive.tar.gz', b'\x1f\x8b\x08\x00'),
    ('file', 'site/assets/notes.md', b'# Notes\n'),
    ('file', 'site/assets/blob.zzq', b'\x00\x01\x02'),
    ('file', 'site/assets/noext', b'plain\n'),
    ('file', 'site/indexlink/index.htm', b'<h1>htm</h1>\n'),
    ('file', 'outside/secret.txt', b'secret\n'),
    ('file', 'outside/dir/index.html', b'<h1>Outside</h1>\n'),
    ('file', 'site-secret/file.txt', b'prefix secret\n'),
    ('dir', 'site/empty', None),
    ('link', 'site/link-inside.txt', 'assets/site.css'),
    ('link', 'site/escape.txt', '../outside/secret.txt'),
    ('link', 'site/escape-dir', '../outside/dir'),
    ('abslink', 'site/abs-escape.txt', 'outside/secret.txt'),
    ('link', 'site/prefix.txt', '../site-secret/file.txt'),
    ('link', 'site/dangling', 'nowhere'),
    ('link', 'site/docs/up', '..'),
    ('link', 'site/indexlink/index.html', '../../outside/secret.txt'),
]

MISSING = '/no-such-file'
IMS = b'GET /assets/site.css HTTP/1.0\r\nIf-Modified-Since: '


def get(target, version=b' HTTP/1.0', headers=b''):
    return b'GET ' + target + version + b'\r\n' + headers + b'\r\n'


# (label, mode, as target, prefix, count, repeat, suffix)
CASES = [
    ('root', '', '', b'GET / HTTP/1.1\r\nHost: preview\r\n\r\n'),
    ('root HEAD', '', '', b'HEAD / HTTP/1.1\r\nHost: preview\r\n\r\n'),
    ('index.html', '', '', get(b'/index.html')),
    ('nested index', '', '', get(b'/about/')),
    ('deeper nested index', '', '', get(b'/blog/post/')),
    ('directory redirect', '', '', get(b'/about')),
    ('redirect keeps query and fragment', '', '', get(b'/blog/post?x=1&y=%20#top')),
    ('redirect drops an empty query', '', '', get(b'/about?')),
    ('redirect HEAD', '', '', b'HEAD /about HTTP/1.0\r\n\r\n'),
    ('redirect of an encoded slash', '', '', get(b'/about%2F')),
    ('index.htm', '', '', get(b'/legacy/')),
    ('listing', '', '', get(b'/docs/')),
    ('listing with a query', '', '', get(b'/docs/?q=<b>&x')),
    ('listing HEAD', '', '', b'HEAD /docs/ HTTP/1.0\r\n\r\n'),
    ('empty listing', '', '', get(b'/empty/')),
    ('nested listing', '', '', get(b'/docs/sub/')),
    ('missing file', '', '', get(b'/missing.html')),
    ('missing file HEAD', '', '', b'HEAD /missing.html HTTP/1.0\r\n\r\n'),
    ('file with a trailing slash', '', '', get(b'/index.html/')),
    ('traversal', '', '', get(b'/../outside/secret.txt')),
    ('encoded traversal', '', '', get(b'/%2e%2e/outside/secret.txt')),
    ('encoded traversal with slashes', '', '', get(b'/%2E%2E%2F%2e%2e%2fetc/passwd')),
    ('encoded slash back inside', '', '', get(b'/docs/..%2findex.html')),
    ('dot segments', '', '', get(b'/assets/./../about/%2e/')),
    ('double slash', '', '', get(b'//etc/passwd')),
    ('double slash redirect', '', '', get(b'//about')),
    ('link out of the directory', '', MISSING, get(b'/escape.txt')),
    ('directory link out', '', MISSING, get(b'/escape-dir/')),
    ('directory link out, no slash', '', MISSING, get(b'/escape-dir')),
    ('file below a directory link out', '', MISSING, get(b'/escape-dir/index.html')),
    ('absolute link out', '', MISSING, get(b'/abs-escape.txt')),
    ('link to a sibling with the same prefix', '', MISSING, get(b'/prefix.txt')),
    ('link out through a link inside', '', MISSING, get(b'/docs/up/escape.txt')),
    ('index.html leading out', '', '/indexlink/index.htm', get(b'/indexlink/')),
    ('dangling link', '', '', get(b'/dangling')),
    ('link inside', '', '', get(b'/link-inside.txt')),
    ('directory link inside', '', '', get(b'/docs/up/')),
    ('css', '', '', get(b'/assets/site.css')),
    ('javascript', '', '', get(b'/assets/app.js')),
    ('json', '', '', get(b'/assets/data.json')),
    ('svg', '', '', get(b'/assets/logo.svg')),
    ('woff2', '', '', get(b'/assets/font.woff2')),
    ('gzip archive', '', '', get(b'/assets/archive.tar.gz')),
    ('markdown', '', '', get(b'/assets/notes.md')),
    ('unknown type', '', '', get(b'/assets/blob.zzq')),
    ('no extension', '', '', get(b'/assets/noext')),
    ('query on a file', '', '', get(b'/index.html?x=1')),
    ('fragment on a file', '', '', get(b'/index.html#top')),
    ('encoded UTF-8 name', '', '', get(b'/docs/%C3%A9.txt')),
    ('encoded space', '', '', get(b'/docs/alpha%20b.txt')),
    ('encoded specials', '', '', get(b'/docs/q%26a%20%3Cx%3E.txt')),
    ('raw UTF-8 name', '', '', get(b'/docs/\xc3\xa9.txt')),
    ('invalid escape', '', '', get(b'/docs/%zz')),
    ('invalid UTF-8 escape', '', '', get(b'/%ff')),
    ('NUL byte', '', MISSING, get(b'/%00')),
    ('control byte', '', '', get(b'/\x01a')),
    ('absolute-form target', '', '', b'GET http://127.0.0.1/about/ HTTP/1.1\r\n\r\n'),
    ('POST', '', '', b'POST / HTTP/1.0\r\nContent-Length: 5\r\n\r\nhello'),
    ('lowercase method', '', '', b'get / HTTP/1.0\r\n\r\n'),
    ('OPTIONS *', '', '', b'OPTIONS * HTTP/1.1\r\n\r\n'),
    ('HTTP/2.0', '', '', get(b'/', b' HTTP/2.0')),
    ('bad version', '', '', get(b'/', b' HTTP/1.x')),
    ('version too long', '', '', get(b'/', b' HTTP/1.12345678901')),
    ('version with quotes', '', '', get(b'/', b' HTTP/\'1"')),
    ('version in latin-1', '', '', get(b'/', b' HTTP/\xe9')),
    ('four words', '', '', get(b'/ x')),
    ('four words in latin-1', '', '', get(b'/\xe9 x')),
    ('extra word', '', '', b'GET / HTTP/1.0 extra\r\n\r\n'),
    ('one word', '', '', b'GET\r\n\r\n'),
    ('HTTP/0.9', '', '', b'GET /about/\r\n\r\n'),
    ('HTTP/0.9 missing', '', '', b'GET /nope\r\n\r\n'),
    ('HTTP/0.9 redirect', '', '', b'GET /about\r\n\r\n'),
    ('HTTP/0.9 POST', '', '', b'POST /\r\n\r\n'),
    ('blank request line', '', '', b'\r\n'),
    ('whitespace request line', '', '', b' \t \r\n'),
    ('If-Modified-Since later', '', '', IMS + b'Fri, 01 Jan 2100 00:00:00 GMT\r\n\r\n'),
    ('If-Modified-Since earlier', '', '', IMS + b'Thu, 01 Jan 1970 00:00:00 GMT\r\n\r\n'),
    ('If-Modified-Since with If-None-Match', '', '', IMS + b'Fri, 01 Jan 2100 00:00:00 GMT\r\nIf-None-Match: "x"\r\n\r\n'),
    ('If-Modified-Since lowercase, RFC 850', '', '',
     b'GET /assets/site.css HTTP/1.0\r\nif-modified-since: Friday, 01-Jan-37 00:00:00 GMT\r\n\r\n'),
    ('If-Modified-Since asctime', '', '', IMS + b'Fri Jan  1 00:00:00 2100\r\n\r\n'),
    ('If-Modified-Since offset', '', '', IMS + b'Fri, 01 Jan 2100 00:00:00 +0100\r\n\r\n'),
    ('If-Modified-Since -0000', '', '', IMS + b'Fri, 01 Jan 2100 00:00:00 -0000\r\n\r\n'),
    ('If-Modified-Since EST', '', '', IMS + b'Fri, 01 Jan 2100 00:00:00 EST\r\n\r\n'),
    ('If-Modified-Since invalid day', '', '', IMS + b'Sat, 31 Feb 2100 00:00:00 GMT\r\n\r\n'),
    ('If-Modified-Since garbage', '', '', IMS + b'yesterday\r\n\r\n'),
    ('If-Modified-Since folded', '', '', IMS + b'Fri, 01 Jan\r\n 2100 00:00:00 GMT\r\n\r\n'),
    ('If-Modified-Since after a non-header line', '', '',
     b'GET /assets/site.css HTTP/1.0\r\nnot a header\r\nIf-Modified-Since: Fri, 01 Jan 2100 00:00:00 GMT\r\n\r\n'),
    ('If-Modified-Since on a listing', '', '', get(b'/docs/', headers=b'If-Modified-Since: Fri, 01 Jan 2100 00:00:00 GMT\r\n')),
    ('If-Modified-Since HEAD', '', '', b'HEAD /index.html HTTP/1.0\r\nIf-Modified-Since: Fri, 01 Jan 2100 00:00:00 GMT\r\n\r\n'),
    ('bare LF line ends', '', '', b'GET /about/ HTTP/1.0\nHost: x\n\n'),
    ('GET with a body', '', '', b'GET / HTTP/1.0\r\nContent-Length: 3\r\n\r\nabc'),
    ('sent in pieces', 'split', '', b'GET /about/ HTTP/1.0\r\nHost: x\r\n\r\n'),
    ('end of input after a header', 'eof', '', b'GET /about/ HTTP/1.0\r\nHost: x'),
    ('end of input in the request line', 'eof', '', b'GET /abo'),
    ('end of input after the request line', 'eof', '', b'GET / HTTP/1.0\r\n'),
    ('99 headers', '', '', (b'GET / HTTP/1.0\r\n', 99, b'X: y\r\n', b'\r\n')),
    ('100 headers', '', '', (b'GET / HTTP/1.0\r\n', 100, b'X: y\r\n', b'\r\n')),
    ('header line too long', '', '', (b'GET / HTTP/1.0\r\nX: ', 65540, b'a', b'\r\n\r\n')),
    ('HEAD header line too long', '', '', (b'HEAD / HTTP/1.0\r\nX: ', 65540, b'a', b'\r\n\r\n')),
    ('request line at the limit', '', '', (b'GET /', 65520, b'a', b' HTTP/1.0\r\n\r\n')),
    ('request line too long', '', '', (b'GET /', 65521, b'a', b' HTTP/1.0\r\n\r\n')),
]

CONCURRENT_A = (b'GET /about/ HTTP/1.0\r\nHost: x\r\n', b'\r\n')
CONCURRENT_B = b'GET / HTTP/1.0\r\n\r\n'


def request_of(case):
    value = case[3]
    if isinstance(value, tuple):
        prefix, count, repeat, suffix = value
        return prefix + repeat*count + suffix, (prefix, count, repeat, suffix)
    return value, (value, 0, b'', b'')


def build_setup(base):
    for kind, path, value in SETUP:
        target = base/path
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'file':
            target.write_bytes(value)
        elif kind == 'dir':
            target.mkdir()
        elif kind == 'link':
            os.symlink(value, target)
        else:
            os.symlink(base/value, target)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def exchange(port, request, mode):
    with socket.create_connection(('127.0.0.1', port), timeout=10) as s:
        try:
            if mode == 'split':
                for i in range(0, len(request), 3):
                    s.sendall(request[i:i+3])
                    time.sleep(0.005)
            else:
                s.sendall(request)
            if mode == 'eof':
                s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        data = b''
        while True:
            try:
                chunk = s.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
    return data


def served_file(directory, target):
    """The file SimpleHTTPRequestHandler serves for target (None for a listing)."""
    handler = http.server.SimpleHTTPRequestHandler.__new__(http.server.SimpleHTTPRequestHandler)
    handler.directory = directory
    if target.startswith('//'):
        target = '/' + target.lstrip('/')
    path = handler.translate_path(target)
    if os.path.isdir(path):
        for index in ('index.html', 'index.htm'):
            if os.path.isfile(os.path.join(path, index)):
                return os.path.join(path, index)
        return None
    return path


def normalized(response, directory=None, target=None):
    if not response:
        return b'(no response)\n'
    end = response.find(b'\r\n\r\n')
    if response.startswith(b'HTTP/') and end >= 0:
        lines = response[:end].split(b'\r\n')
        for i, line in enumerate(lines):
            for name, shown in ((b'Date: ', b'DATE'), (b'Server: ', b'SERVER'), (b'Last-Modified: ', b'LAST-MODIFIED')):
                if line.startswith(name):
                    lines[i] = name + shown
            if line.startswith(b'Content-type: ') and lines[0].startswith(b'HTTP/1.0 200 ') and directory:
                path = served_file(directory, target)
                if path is not None:
                    lines[i] = b'Content-type: ' + build.content_type(path).encode()
        response = b'\r\n'.join(lines) + response[end:]
    return response.replace(b'\r', b'\\r') + b'\n'


def log_lines(text):
    out = b''
    for line in text.splitlines(keepends=True):
        start, stop = line.find(b'['), line.find(b']')
        out += line[:start] + b'[TIME]' + line[stop+1:] if start >= 0 and stop > start else line
    return out


class Server:
    def __init__(self, directory, port):
        self.process = subprocess.Popen([sys.executable, '-I', '-u', '-c', HARNESS, str(ROOT), 'preview', '--dir', str(directory),
                                         '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines = queue.Queue()
        threading.Thread(target=self.read, daemon=True).start()
        for _ in range(500):
            try:
                socket.create_connection(('127.0.0.1', port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.01)

    def read(self):
        for line in self.process.stderr:
            self.lines.put(line)

    def log(self):
        time.sleep(0.05)
        out = b''
        while not self.lines.empty():
            out += self.lines.get()
        return out


def target_of(request):
    words = request.split(b'\n', 1)[0].rstrip(b'\r\n').split()
    return words[1].decode('latin-1') if len(words) >= 2 else ''


def first_line(request):
    return request.split(b'\n', 1)[0].rstrip(b'\r\n')


def cli(directory, port):
    result = subprocess.run([sys.executable, '-I', '-c', HARNESS, str(ROOT), 'preview', '--dir', str(directory), '--port', str(port)],
                            capture_output=True, timeout=30)
    return result.stdout, result.stderr, result.returncode


def shown_cli(stdout, stderr, code, port=None):
    out = b'--- stdout\n' + stdout + b'--- stderr\n' + stderr + b'exit %d\n' % code
    return out.replace(b':%d' % port, b':PORT') if port else out


def reference(base):
    build_setup(base)
    site = base/'site'
    port = free_port()
    server = Server(site, port)
    out = b'=== serve\n'
    for case in CASES:
        label, mode, as_target = case[:3]
        request, _ = request_of(case)
        sent = request
        if as_target:
            line = first_line(request)
            sent = request.replace(line, line.replace(target_of(request).encode('latin-1'), as_target.encode(), 1), 1)
        response = exchange(port, sent, mode)
        log = server.log()
        if as_target:
            log = log.replace(first_line(sent), first_line(request))
        out += b'=== ' + label.encode() + b'\n' + normalized(response, str(site), target_of(sent)) + b'--- log\n' + log_lines(log)
    with socket.create_connection(('127.0.0.1', port), timeout=10) as a:
        a.sendall(CONCURRENT_A[0])
        time.sleep(0.05)
        b = exchange(port, CONCURRENT_B, '')
        a.sendall(CONCURRENT_A[1])
        first = b''
        while chunk := a.recv(65536):
            first += chunk
    out += (b'=== concurrent\n--- B\n' + normalized(b, str(site), '/') + b'--- A\n' + normalized(first, str(site), '/about/') +
            b'--- log\n' + log_lines(server.log()))
    server.process.send_signal(signal.SIGINT)
    code = server.process.wait(timeout=10)
    stdout = server.process.stdout.read()
    out = out.replace(b'=== serve\n', b'=== serve\n' + stdout.replace(b':%d' % port, b':PORT'), 1)
    out += b'=== stop\nexit %d\n' % code
    out += b'=== no index.html\n' + shown_cli(*cli(base/'outside', port))
    out += b'=== no directory\n' + shown_cli(*cli(base/'nope', port))
    held = socket.socket()
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(('127.0.0.1', port))
    held.listen(8)
    out += b'=== port in use\n' + shown_cli(*cli(site, port), port)
    held.close()
    stdout, stderr, code = cli(site, 70000)
    assert b'OverflowError' in stderr, stderr
    out += b'=== port 70000\n' + shown_cli(stdout, b'neokit: bind(): port must be 0-65535.\n', 1)
    return out


MANIFEST = '''# Generated by generate.py from compiler/cli.py; do not edit.
# Run with the Neo 0.2.6 branch build: neo test tests/cli_golden/preview/preview.neo.toml

[[test]]
name = "preview server against SimpleHTTPRequestHandler"
file = "driver.neo"
args = ["fixture"]
stdout-file = "golden/fixture.out"
timeout = 120
'''


def main():
    GOLDEN.mkdir(exist_ok=True)
    rows = []
    for kind, path, value in SETUP:
        assert '\t' not in path and '\n' not in path
        rows.append('\t'.join([kind, path] + ([] if value is None else [value.hex() if kind == 'file' else value])))
    (HERE/'setup.tsv').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    rows = []
    for case in CASES:
        label, mode, as_target = case[:3]
        _, (prefix, count, repeat, suffix) = request_of(case)
        rows.append('\t'.join([label, mode, as_target, prefix.hex(), str(count), repeat.hex(), suffix.hex()]))
    (HERE/'cases.tsv').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    base = Path(tempfile.mkdtemp(prefix='neokit-preview-golden-'))
    try:
        (GOLDEN/'fixture.out').write_bytes(reference(base))
    finally:
        shutil.rmtree(base)
    (HERE/'preview.neo.toml').write_text(MANIFEST)


if __name__ == '__main__':
    main()
