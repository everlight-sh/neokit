#!/usr/bin/env python3
"""Large responses through both Linux HTTP backends.

A large keep-alive response with nothing queued ahead of it is sent to the
socket straight away and only the rest is buffered. This checks that, with
io_uring and with epoll: pipelined responses (large and small mixed) arrive
whole and in order to a slow reader with a tiny receive buffer, so sends are
cut short; Connection: close and HTTP/1.0 responses arrive whole before the
close; 32 concurrent connections each fetch large pages. Every body is checked
by tests/catalog_oracle.py.

  python3 tests/large_responses.py build/catalog/server

where build/catalog is ./neokit build --app tests/catalog_fixture --out build/catalog.
Each backend gets a fresh port: a server restarted on a port an io_uring
server has just left can see connections reset while the old ring tears down.
"""
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.catalog_oracle import validate  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); return port


def start(binary, port, env):
    proc = subprocess.Popen([binary, str(port)], env={**os.environ, "NEO_THREADS": "1", **env},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); return proc
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("server did not start")


def connect(port, rcvbuf=None):
    s = socket.socket()
    if rcvbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    s.connect(("127.0.0.1", port)); s.settimeout(10); return s


def read_response(s, buf):
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(65536)
        if not chunk: raise EOFError("closed in headers")
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    length = int(next(l.split(b":")[1] for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")))
    while len(rest) < length:
        chunk = s.recv(65536)
        if not chunk: raise EOFError("closed in body")
        rest += chunk
    return rest[:length], rest[length:]


def request(path, n, seed, token, extra=b"", version=b"HTTP/1.1"):
    return f"GET {path}?n={n}&seed={seed}&token={token} ".encode() + version + b"\r\nHost: x\r\n" + extra + b"\r\n"


def pipelined(port):
    plan = [("/large", 1000), ("/", 3), ("/large", 1000), ("/large", 700), ("/interactive", 50), ("/large", 1000)]
    s = connect(port, rcvbuf=4096)
    s.sendall(b"".join(request(p, n, k, f"t{k}") for k, (p, n) in enumerate(plan)))
    time.sleep(0.4)  # let the socket fill, so sends are cut short
    buf = b""
    for k, (p, n) in enumerate(plan):
        body, buf = read_response(s, buf)
        validate(body, n, k, f"t{k}", p == "/interactive")
    s.close()
    return len(plan)


def closing(port):
    for extra, version in [(b"Connection: close\r\n", b"HTTP/1.1"), (b"", b"HTTP/1.0")]:
        s = connect(port); s.sendall(request("/large", 1000, 5, "close", extra, version))
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        validate(data.split(b"\r\n\r\n", 1)[1], 1000, 5, "close", False)
        s.close()
    return 2


def concurrent(port):
    errors, done = [], [0]
    def worker(w):
        try:
            s = connect(port); buf = b""
            for i in range(20):
                seed = (w * 31 + i) % 1000
                s.sendall(request("/large", 1000, seed, f"w{w}"))
                body, buf = read_response(s, buf)
                validate(body, 1000, seed, f"w{w}", False)
                done[0] += 1
            s.close()
        except Exception as e:
            errors.append(repr(e))
    threads = [threading.Thread(target=worker, args=(w,)) for w in range(32)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors, errors[:3]
    return done[0]


def main():
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/catalog/server"
    for name, env in [("io_uring", {}), ("epoll", {"NEO_HTTP_URING": "0"})]:
        port = free_port(); proc = start(binary, port, env)
        try:
            print(f"PASS {name}: {pipelined(port)} pipelined, {closing(port)} closing, {concurrent(port)} concurrent responses", flush=True)
        finally:
            os.killpg(proc.pid, signal.SIGTERM); proc.wait()


if __name__ == "__main__":
    main()
