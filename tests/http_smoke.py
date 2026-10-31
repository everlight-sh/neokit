#!/usr/bin/env python3
"""Exercise native SSR, asset embedding, framing, export and independent models."""
import argparse
import concurrent.futures
import contextlib
import functools
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def running(binary, workers=1):
    port=free_port()
    process=subprocess.Popen([str(binary),str(port)],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                             env={**os.environ,'AXON_THREADS':str(workers)})
    try:
        for _ in range(100):
            if process.poll() is not None:raise AssertionError(process.stderr.read().decode())
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):break
            except OSError:time.sleep(.03)
        else:raise AssertionError('Server did not start')
        yield port
    finally:
        if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
        process.wait(timeout=10)
        process.stderr.close()


def request(port,path='/',method='GET',headers=None):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
    try:
        connection.request(method,path,headers=headers or {})
        response=connection.getresponse();body=response.read()
        if method!='HEAD':assert int(response.getheader('Content-Length'))==len(body)
        return response.status,dict(response.getheaders()),body
    finally:connection.close()


def audit(binary):
    assert not subprocess.check_output(['nm','-u',str(binary)],text=True).strip()
    if sys.platform=='linux':
        assert 'INTERP' not in subprocess.check_output(['readelf','-l',str(binary)],text=True)
        assert 'NEEDED' not in subprocess.check_output(['readelf','-d',str(binary)],text=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--out',type=Path,default=ROOT/'build/studio')
    args=parser.parse_args()
    if not args.binary:subprocess.run([str(ROOT/'web'),'build','--out',str(args.out)],cwd=ROOT,check=True)
    binary=(args.binary or args.out/'server').resolve()
    manifest=json.loads((args.out/'manifest.json').read_text())
    audit(binary)
    for workers in (1,2):
        with running(binary,workers) as port:
            status,headers,home=request(port)
            assert status==200 and b'Build your next' in home and b'data-web-widget="counter"' in home
            assert b'data-web-form' in home and b'LIVE SERVER RENDER' in home
            assert headers['Cache-Control']=='no-store' and headers['X-Content-Type-Options']=='nosniff'
            assert b'Rendered on request' in home
            stamp=int(re.search(rb'Rendered on request[^<]*?([0-9]{10,})',home)[1])
            time.sleep(.02)
            next_home=request(port)[2]
            assert int(re.search(rb'Rendered on request[^<]*?([0-9]{10,})',next_home)[1])>stamp
            head=request(port,method='HEAD');assert head[0]==200 and head[2]==b'' and int(head[1]['Content-Length'])>500
            assert request(port,method='POST')[0]==405
            assert request(port,'/missing')[0]==404
            assert request(port,'/_web/../src/lib.ax')[0]==404
            assert request(port,'/notes/')[0]==404
            for path in ('/notes/%2f','/notes/%2e%2e','/notes/%ff','/notes/%00','/notes/%zz'):
                assert request(port,path)[0]==404,path
            for path in ('/notes/native','/notes/static','/notes/islands','/about','/about/','/stress'):
                assert request(port,path)[0]==200,path
            about=request(port,'/about')[2]
            assert b'<script' not in about and about==(args.out/'snapshots/about/index.html').read_bytes()
            value='<script>alert("hi")</script> & café'
            escaped='&lt;script&gt;alert(&quot;hi&quot;)&lt;/script&gt; &amp; café'.encode()
            _,_,greeting=request(port,'/greet?name='+quote(value),headers={'X-Web-Fragment':'1'})
            assert escaped in greeting and b'<!doctype' not in greeting.lower() and b'<script>' not in greeting
            full=request(port,'/greet?name=Marcos')[2]
            assert b'<!doctype html>' in full.lower() and b'Hello, Marcos' in full
            assert escaped in request(port,'/?name='+quote(value))[2]
            dynamic=request(port,'/notes/'+quote('<img src=x onerror=alert(1)>',safe=''))
            assert dynamic[0]==200 and b'&lt;img src=x onerror=alert(1)&gt;' in dynamic[2] and b'<img src=x' not in dynamic[2]
            for url,info in manifest['files'].items():
                status,asset_headers,data=request(port,url)
                assert status==200 and len(data)==info['bytes'] and hashlib.sha256(data).hexdigest()==info['sha256']
                if url.startswith('/_web/'):assert 'immutable' in asset_headers['Cache-Control']
                if url.endswith('.js'):assert asset_headers['Content-Type'].startswith('text/javascript')
            for count in (100,1000):
                status,_,stress=request(port,'/stress?n='+str(count))
                assert status==200 and stress.count(b'<tr data-row-id=')==count
                assert b'data-web-widget="list-stress"' in stress and b'Full redraw (comparison)' in stress
                assert stress.count(b'data-note aria-label=')==count
            assert request(port,'/stress?n=-1')[2].count(b'<tr data-row-id=')==1000
            api=json.loads(request(port,'/api/status')[2]);assert api['renderer']=='native' and api['react'] is False and api['stamp']>0
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
            for path in ('/','/about','/notes/native','/api/status')*3:
                connection.request('GET',path);response=connection.getresponse();assert response.status==200;response.read()
            connection.close()
            def parallel(i):return request(port,'/greet?name=visitor'+str(i),headers={'X-Web-Fragment':'1'})
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                for i,result in enumerate(pool.map(parallel,range(48))):assert result[0]==200 and ('Hello, visitor'+str(i)).encode() in result[2]
            with socket.create_connection(('127.0.0.1',port),timeout=10) as sock:
                sock.sendall(b'HEAD /about HTTP/1.1\r\nHost: localhost\r\n\r\nGET /api/status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                stream=sock.makefile('rb');assert stream.readline().startswith(b'HTTP/1.1 200')
                while stream.readline()!=b'\r\n':pass
                assert stream.readline().startswith(b'HTTP/1.1 200')
                fields={}
                while (line:=stream.readline())!=b'\r\n':
                    key,value=line.split(b':',1);fields[key.lower()]=value.strip()
                assert json.loads(stream.read(int(fields[b'content-length'])))['react'] is False
                assert stream.read(1)==b'';stream.close()
            print(f'PASS native SSR, fresh data, escaped fragments, dynamic routes, HEAD/pipeline, assets, API, 48 concurrent requests ({workers} workers)',flush=True)
    export=args.out.parent/'test-export'
    subprocess.run([str(ROOT/'web'),'export','--out',str(args.out),'--dest',str(export)],cwd=ROOT,check=True)
    home=(export/'index.html').read_bytes()
    assert b'STATIC EXPORT' in home and b'data-web-form' not in home and b'data-web-widget="counter"' in home
    assert b'<script' not in (export/'about/index.html').read_bytes()
    assert (export/'stress/index.html').read_bytes().count(b'<tr data-row-id=')==1000
    assert not (export/'greet/index.html').exists() and not (export/'api/status/index.html').exists()
    handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(export))
    static=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=static.serve_forever,daemon=True);thread.start()
    try:
        assert request(static.server_port)[0]==200
        for url in manifest['files']:assert request(static.server_port,url)[0]==200
    finally:static.shutdown();static.server_close();thread.join()
    strict=subprocess.run([str(ROOT/'web'),'export','--out',str(args.out),'--dest',str(export),'--strict'],cwd=ROOT,capture_output=True,text=True)
    assert strict.returncode!=0 and 'Server-only routes' in strict.stderr
    print('PASS static export: 6 pages, same assets, interactive module references, zero-JS about page; explicit server-only omissions and strict rejection')


if __name__=='__main__':main()
