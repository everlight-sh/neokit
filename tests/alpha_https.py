"""Verify the Meridian Compose deployment with a trusted local test CA."""
import argparse
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
import uuid


def check(framework, http_port, https_port, output):
    name = 'neokit-alpha-' + uuid.uuid4().hex[:10]
    compose = ['docker', 'compose', '-p', name, '-f', str(framework/'deploy/meridian.compose.yaml'),
               '-f', str(framework/'deploy/meridian.local.compose.yaml')]
    env = {**os.environ, 'DOMAIN':'localhost', 'NEOKIT_BIND_IP':'127.0.0.1',
           'NEOKIT_HTTP_PORT':str(http_port), 'NEOKIT_HTTPS_PORT':str(https_port),
           'NEOKIT_APP_IMAGE':name+':verified'}

    def call(*args, **kwargs):
        return subprocess.run([*compose, *args], env=env, cwd=framework, check=True, **kwargs)

    try:
        call('up', '--build', '--wait', '--wait-timeout', '60')
        with tempfile.TemporaryDirectory(prefix='neokit-https-') as temporary:
            certificate = Path(temporary)/'root.crt'
            call('cp', 'proxy:/data/caddy/pki/authorities/local/root.crt', str(certificate))
            tls = ssl.create_default_context(cafile=str(certificate))

            def connect(method, path, headers=None):
                connection = http.client.HTTPSConnection('localhost', https_port, context=tls, timeout=40)
                try:
                    connection.request(method, path, headers=headers or {})
                    return connection, connection.getresponse()
                except Exception:
                    connection.close()
                    raise

            def request(method, path):
                connection, response = connect(method, path)
                try:
                    return response.status, response.read()
                finally:
                    connection.close()

            def wait_for_health():
                # A VM-backed Docker port forward can lag container health.
                deadline = time.monotonic()+10
                while True:
                    try:
                        assert request('GET', '/healthz') == (200, b'{"ok":true}')
                        return
                    except ConnectionRefusedError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.2)

            def event(response):
                fields = {}
                while line := response.readline():
                    if line.strip() and b': ' in line:
                        key, value = line.rstrip(b'\r\n').split(b': ', 1)
                        fields[key] = value
                    elif not line.strip():
                        if b'id' in fields:
                            return fields[b'id'].decode(), json.loads(fields[b'data'])
                        fields = {}
                raise AssertionError('Event stream ended early')

            wait_for_health()
            status, page = request('GET', '/')
            assert status == 200 and b'Meridian' in page and b'data-live' in page
            status, page = request('GET', '/customers?records=10000')
            assert status == 200 and page.count(b'data-customer-id=') == 25
            assert request('POST', '/')[0] == 405
            connection, response = connect('GET', '/api/events')
            assert response.status == 200 and 'text/event-stream' in response.getheader('Content-Type')
            first, _ = event(response)
            second, data = event(response)
            connection.close()
            response.close()
            connection, response = connect('GET', '/api/events', {'Last-Event-ID':first})
            assert event(response) == (second, data)
            app = call('ps', '-q', 'app', capture_output=True, text=True).stdout.strip()
            state = json.loads(subprocess.check_output(['docker','inspect',app], text=True))[0]
            assert not state['HostConfig']['PortBindings']
            assert state['HostConfig']['ReadonlyRootfs'] and state['Config']['User'] == '65532:65532'
            started = time.monotonic()
            call('stop', 'app')
            shutdown_seconds = time.monotonic()-started
            exit_code = subprocess.check_output(['docker','inspect','--format','{{.State.ExitCode}}',app],text=True).strip()
            assert exit_code == '0' and shutdown_seconds < 37, (exit_code, shutdown_seconds)
            while response.readline():
                pass
            response.close()
            connection.close()
            call('up', '-d', '--no-build', '--wait', '--wait-timeout', '60')
            wait_for_health()
            output.write_text(json.dumps({'tls':'verified local CA and localhost hostname',
                'meridian_ssr':True,'directory_rows':25,'proxy_mutations':405,
                'sse_replay':True,'nonroot_readonly_container':True,
                'shutdown_exit_code':int(exit_code),'shutdown_seconds':shutdown_seconds,
                'restart_without_rebuild':True},indent=2)+'\n')
            print('PASS HTTPS, read-only Meridian, SSE replay, graceful stop and restart')
    finally:
        call('down', '--volumes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--framework', type=Path, required=True)
    parser.add_argument('--http-port', type=int, default=8189)
    parser.add_argument('--https-port', type=int, default=8449)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    check(args.framework.resolve(), args.http_port, args.https_port, args.out.resolve())
