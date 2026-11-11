"""Exercise the release archive without a checkout, package cache or global SDK."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tarfile
import tempfile
import time


def unpack(archive, destination):
    with tarfile.open(archive) as payload:
        for member in payload:
            relative = Path(member.name)
            assert member.isfile() and not relative.is_absolute() and '..' not in relative.parts, member.name
            path = destination/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload.extractfile(member).read())
            path.chmod(member.mode)
    roots = list(destination.iterdir())
    assert len(roots) == 1 and (roots[0]/'MANIFEST.json').is_file(), roots
    return roots[0]


def get(port, path):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', path, headers={'Connection':'close'})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def check(archive):
    with tempfile.TemporaryDirectory(prefix='neokit-alpha-clean-') as temporary:
        root = Path(temporary).resolve()
        home = root/'home'
        home.mkdir()
        env = {'PATH':os.environ['PATH'], 'HOME':str(home), 'LC_ALL':'C', 'NEO_THREADS':'1'}
        source = unpack(archive, root/'download')
        prefix = root/'installed tools'
        subprocess.run([str(source/'install.sh'), str(prefix)], env=env, cwd=root, check=True)
        source.rename(root/'removed-download')
        neokit = prefix/'bin/neokit'
        subprocess.run([str(neokit), 'doctor'], env=env, cwd=root, check=True)
        project = root/'first-app'
        subprocess.run([str(neokit), 'init', '--standalone', str(project)], env=env, cwd=root, check=True)
        prefix.rename(root/'removed-installation')
        moved = root/'relocated project'
        project.rename(moved)
        lock = json.loads((moved/'neo.lock').read_text())
        assert all((moved/pkg['path']).resolve().is_relative_to(moved) for pkg in lock['packages'].values())
        subprocess.run(['./neokit', 'build'], env=env, cwd=moved, check=True)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(['./neokit', 'serve', '--no-build', '--port', str(port)],
                                   env=env, cwd=moved, start_new_session=True)
        try:
            for _ in range(100):
                assert process.poll() is None
                try:
                    status, body = get(port, '/')
                    break
                except OSError:
                    time.sleep(.05)
            else:
                raise AssertionError('Starter server did not listen')
            assert status == 200 and b'<h1>Hello from NeoKit</h1>' in body
            manifest = json.loads((moved/'.neokit/build/manifest.json').read_text())
            asset = manifest['assets']['client.js']
            assert asset.encode() in body and get(port, asset)[0] == 200
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                exit_code = process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        assert exit_code == 0
        subprocess.run(['./neokit', 'export'], env=env, cwd=moved, check=True)
        assert b'<h1>Hello from NeoKit</h1>' in (root/'relocated project-export/index.html').read_bytes()
        print('PASS cold install, moved standalone starter, native SSR, browser asset and static export')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if args.sha256:
        assert hashlib.sha256(args.archive.read_bytes()).hexdigest() == args.sha256.lower()
    check(args.archive.resolve())
