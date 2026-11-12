#!/usr/bin/env python3
"""Checks of cli/preview.neo's server that the golden does not make.

Run on the test box from the repository root, with NEO_HOME set to the
Neo 0.2.6 branch build:

    $NEO_HOME/neo build tests/cli_golden/preview/driver.neo -o build/preview-driver
    python3 tests/cli_golden/preview/parity.py build/preview-driver [--slow]

The driver's `serve DIR PORT` mode is `neokit preview --dir DIR --port
PORT`. The checks:

1. the listener is bound to 127.0.0.1 only (ss lists 127.0.0.1:PORT, and
   the machine's other addresses refuse the port);
2. a 24 MiB file arrives whole, also for a client reading slowly (the
   server's writes then wait for the socket), and HEAD gives its length;
3. 100 idle connections do not keep a request from being served, and
   all of them are served once they send their requests;
4. compiler/cli.py's preview gives the same bytes for the large file;
5. SIGINT stops the server with status 0 and nothing more on stdout or
   stderr than the line it printed and its log;
6. with --slow, an idle connection is closed after 60 seconds.

Prints one line per check and exits 1 when one fails.
"""
import hashlib
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
FAILED = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok or not detail else f': {detail}'))
    if not ok:
        FAILED.append(name)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start(argv, port):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(500):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=1).close()
            return process
        except OSError:
            time.sleep(0.01)
    raise SystemExit('server did not start')


def fetch(port, request, slow=False):
    with socket.create_connection(('127.0.0.1', port), timeout=30) as s:
        s.sendall(request)
        data = b''
        while True:
            chunk = s.recv(4096 if slow else 1 << 20)
            if not chunk:
                return data
            data += chunk
            if slow and len(data) < 2*1024*1024:
                time.sleep(0.001)


def body(response):
    return response[response.find(b'\r\n\r\n')+4:]


def other_addresses():
    out = subprocess.run(['hostname', '-I'], capture_output=True, text=True).stdout.split()
    return [address for address in out if ':' not in address and not address.startswith('127.')]


def main():
    driver = os.path.abspath(sys.argv[1])
    slow = '--slow' in sys.argv[2:]
    base = Path(tempfile.mkdtemp(prefix='neokit-preview-parity-'))
    try:
        site = base/'site'
        site.mkdir()
        (site/'index.html').write_text('<h1>Home</h1>\n')
        big = os.urandom(24*1024*1024)
        (site/'big.bin').write_bytes(big)
        digest = hashlib.sha256(big).hexdigest()
        port = free_port()
        server = start([driver, 'serve', str(site), str(port)], port)

        listening = subprocess.run(['ss', '-ltnH', f'sport = :{port}'], capture_output=True, text=True).stdout.split('\n')
        locals_ = [line.split()[3] for line in listening if line.strip()]
        check('bound to 127.0.0.1 only', locals_ == [f'127.0.0.1:{port}'], str(locals_))
        refused = []
        for address in other_addresses():
            try:
                socket.create_connection((address, port), timeout=2).close()
            except OSError:
                refused.append(address)
        check('other addresses refuse the port', refused == other_addresses(), f'{refused} of {other_addresses()}')

        response = fetch(port, b'GET /big.bin HTTP/1.0\r\n\r\n')
        check('a 24 MiB file arrives whole', hashlib.sha256(body(response)).hexdigest() == digest, f'{len(body(response))} bytes')
        response = fetch(port, b'GET /big.bin HTTP/1.0\r\n\r\n', slow=True)
        check('... also for a slow reader', hashlib.sha256(body(response)).hexdigest() == digest, f'{len(body(response))} bytes')
        response = fetch(port, b'HEAD /big.bin HTTP/1.0\r\n\r\n')
        check('HEAD gives its length', b'\r\nContent-Length: %d\r\n' % len(big) in response and response.endswith(b'\r\n\r\n'))

        idle = [socket.create_connection(('127.0.0.1', port), timeout=10) for _ in range(100)]
        started = time.monotonic()
        response = fetch(port, b'GET / HTTP/1.0\r\n\r\n')
        check('100 idle connections do not hold up a request', response.startswith(b'HTTP/1.0 200 OK\r\n')
              and time.monotonic()-started < 2, f'{time.monotonic()-started:.2f} s')
        for s in idle:
            s.sendall(b'GET /index.html HTTP/1.0\r\n\r\n')
        answers = []
        for s in idle:
            data = b''
            while chunk := s.recv(65536):
                data += chunk
            answers.append(data)
            s.close()
        check('... and all of them are served', all(a.startswith(b'HTTP/1.0 200 OK\r\n') and a.endswith(b'<h1>Home</h1>\n') for a in answers))

        python_port = free_port()
        harness = ('import sys; sys.path.insert(0, sys.argv[1]); import compiler.cli as cli; '
                   'sys.argv = ["neokit", *sys.argv[2:]]; cli.main()')
        python = start([sys.executable, '-I', '-c', harness, str(ROOT), 'preview', '--dir', str(site), '--port', str(python_port)], python_port)
        reference = fetch(python_port, b'GET /big.bin HTTP/1.0\r\n\r\n')
        python.send_signal(signal.SIGINT)
        python.wait(timeout=10)
        check('Python serves the same bytes', body(reference) == big)

        if slow:
            s = socket.create_connection(('127.0.0.1', port), timeout=90)
            s.sendall(b'GET / HTTP/1.0\r\n')
            started = time.monotonic()
            closed = s.recv(65536)
            took = time.monotonic()-started
            s.close()
            check('an idle connection is closed after 60 seconds', closed == b'' and 59 <= took <= 63, f'{closed!r} after {took:.1f} s')

        server.send_signal(signal.SIGINT)
        code = server.wait(timeout=10)
        stdout, stderr = server.stdout.read(), server.stderr.read()
        log = [line for line in stderr.decode().splitlines() if not line.startswith('127.0.0.1 - - [')]
        check('SIGINT stops it with status 0', code == 0, f'status {code}')
        check('... printing only the line and the log', stdout == f'Static export preview: http://127.0.0.1:{port}\n'.encode() and log == [],
              f'{stdout!r} {log!r}')
    finally:
        shutil.rmtree(base)
    sys.exit(1 if FAILED else 0)


if __name__ == '__main__':
    main()
