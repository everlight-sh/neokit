"""HTTP integration for routing, typed templates, mutations, uploads and streaming."""
import http.client
import json
from pathlib import Path
import socket
import time
import tempfile
import gzip
import os
import signal
import subprocess
from tests.http_smoke import running, request

ROOT=Path(__file__).resolve().parents[1]

def call(port,path,method='GET',body=None,headers=None):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
    try:
        connection.request(method,path,body=body.encode('utf-8') if isinstance(body,str) else body,headers=headers or {})
        response=connection.getresponse()
        return response.status,dict(response.getheaders()),response.read()
    finally:connection.close()

def main(out=None):
    out=Path(out or ROOT/'build/features').resolve()
    with tempfile.TemporaryDirectory(prefix='neokit-feature-cache-') as cache, running(out/'server',env={'NEOKIT_CACHE_DIR':cache,'NEOKIT_REVALIDATE_TOKEN':'test-revalidation-secret-at-least-32-bytes'}) as port:
        status,_,home=call(port,'/')
        assert status==200 and b'&lt;first&gt;' in home and b'<article><h2>Features</h2>' in home
        assert b'<li>0</li><li>1</li><li>2</li>' in home and b'href="/org/acme/items/42"' in home
        status,_,item=call(port,'/org/acme/items/42')
        assert status==200 and b'acme / 42' in item and b'data-web-layout=' in item and b'Organization navigation' in item
        assert b'Docs: </h1>' in call(port,'/docs')[2]
        assert b'Docs: a/b' in call(port,'/docs/a/b')[2]
        for path in ['/org/acme/items','/docs/%2e%2e','/docs/%2f','/missing']:
            status,_,body=call(port,path);assert status==404 and b'Feature page missing' in body,path
        for method in ['POST','PATCH','DELETE']:
            status,headers,body=call(port,'/api/42',method,'name=caf%C3%A9',{'Content-Type':'application/x-www-form-urlencoded'})
            assert status==201 and headers['X-NeoKit']=='features'
            assert json.loads(body)=={'method':method,'id':'42','name':'café'}
        status,headers,_=call(port,'/api/42')
        assert status==405 and headers['Allow']=='POST, PATCH, DELETE, OPTIONS'
        assert call(port,'/','POST')[0]==405
        # Binary payload above the old 64 KiB input ceiling, including NUL/UTF-8.
        payload=bytes(range(256))*4096
        boundary='NeoKitTestBoundary'
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+payload+f'\r\n--{boundary}--\r\n'.encode())
        status,_,result=call(port,'/api/upload','POST',body,{'Content-Type':f'multipart/form-data; boundary={boundary}'})
        assert status==201 and json.loads(result)=={'name':'test.bin','bytes':len(payload)},(status,result)
        assert call(port,'/api/upload','POST',body[:-10],{'Content-Type':f'multipart/form-data; boundary={boundary}'})[0]==400
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
        connection.request('POST','/api/upload',body=(body[i:i+4096] for i in range(0,len(body),4096)),headers={'Content-Type':f'multipart/form-data; boundary={boundary}'},encode_chunked=True)
        response=connection.getresponse()
        assert response.status==201 and json.loads(response.read())=={'name':'test.bin','bytes':len(payload)}
        # Reuse the connection after a chunked body: framing must consume exactly one request.
        connection.request('GET','/healthz');response=connection.getresponse()
        assert response.status==200 and json.loads(response.read())=={'ok':True}
        connection.close()
        for framing in (b'Content-Length: 0\r\nTransfer-Encoding: chunked',b'Transfer-Encoding: gzip',b'Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked',b'Content-Length: 1\r\nContent-Length: 1'):
            with socket.create_connection(('127.0.0.1',port),timeout=5) as connection:
                connection.sendall(b'POST /api/42 HTTP/1.1\r\nHost: localhost\r\n'+framing+b'\r\nConnection: close\r\n\r\n0\r\n\r\n')
                wire=connection.recv(65536)
                assert not wire or wire.startswith(b'HTTP/1.1 400'),wire[:100]
        with socket.create_connection(('127.0.0.1',port),timeout=5) as connection:
            # An earlier buffered reply must precede the streaming reply.
            connection.sendall(b'GET /api/a HTTP/1.1\r\nHost: localhost\r\n\r\nGET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n')
            wire=b'';begin=time.monotonic()
            while b'Shell ready' not in wire:
                part=connection.recv(65536);assert part;wire+=part
            first=time.monotonic()
            assert b'Slow data ready' not in wire
            while True:
                part=connection.recv(65536)
                if not part:break
                wire+=part
            assert time.monotonic()-first>.2
            assert wire.index(b'405 Method Not Allowed')<wire.index(b'200 OK')
            assert b'Transfer-Encoding: chunked' in wire and wire.endswith(b'0\r\n\r\n') and b'Slow data ready' in wire
        status,_,result=call(port,'/actions/typed','POST','{"title":"Typed café","count":42,"enabled":true}',{'Content-Type':'application/json'})
        assert status==200 and json.loads(result)=={'title':'Typed café','count':42,'enabled':True},(status,result)
        for value in ['{"title":"x","count":101,"enabled":true}','{"title":"x","count":"42","enabled":true}','[]','{bad']:
            assert call(port,'/actions/typed','POST',value,{'Content-Type':'application/json'})[0] in (400,422)
        first=call(port,'/cached');assert first[1]['X-NeoKit-Cache']=='MISS'
        time.sleep(.02);second=call(port,'/cached');assert second[1]['X-NeoKit-Cache']=='HIT' and first[2]==second[2]
        assert 'public' in second[1]['Cache-Control']
        private=call(port,'/cached',headers={'Cookie':'session=anything'})
        assert 'X-NeoKit-Cache' not in private[1] and private[1]['Cache-Control']=='no-store'
        assert call(port,'/invalidate','POST')[0]==200
        refreshed=call(port,'/cached');assert refreshed[1]['X-NeoKit-Cache']=='MISS'
        time.sleep(1.05)
        stale=call(port,'/cached');assert stale[1]['X-NeoKit-Cache']=='STALE' and stale[2]==refreshed[2]
        worker=subprocess.Popen([str(out/'revalidate')],env={**os.environ,'NEO_THREADS':'1','NEOKIT_CACHE_DIR':cache,'NEOKIT_REVALIDATE_TOKEN':'test-revalidation-secret-at-least-32-bytes','NEOKIT_REVALIDATE_PORT':str(port)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        try:
            until=time.monotonic()+10
            while time.monotonic()<until:
                assert worker.poll() is None,worker.stderr.read().decode()
                updated=call(port,'/cached')
                if updated[2]!=refreshed[2] and updated[1]['X-NeoKit-Cache']=='HIT':break
                time.sleep(.05)
            else:raise AssertionError('Native background revalidation did not refresh the page')
        finally:
            os.killpg(worker.pid,signal.SIGTERM);worker.wait(timeout=10);worker.stderr.close()
        assert b'<meta name="description" content="Typed routes &amp; native pages">' in home
        assert b'https://features.test/docs/a/b' in call(port,'/sitemap.xml')[2]
        assert b'Sitemap: https://features.test/sitemap.xml' in call(port,'/robots.txt')[2]
        manifest=json.loads((out/'manifest.json').read_text())
        script=manifest['assets']['client.js']
        normal=call(port,script)
        packed=call(port,script,headers={'Accept-Encoding':'br, gzip;q=0.5'})
        assert packed[1]['Content-Encoding']=='gzip' and gzip.decompress(packed[2])==normal[2]
        assert 'Content-Encoding' not in call(port,script,headers={'Accept-Encoding':'gzip;q=0'})[1]
        assert call(port,script,headers={'If-None-Match':normal[1]['ETag']})[0]==304
        assert json.loads(call(port,'/healthz')[2])=={'ok':True}
        print('PASS typed actions, validation, tagged cache hits/invalidation, personalized bypass, metadata, sitemap, gzip negotiation and conditional assets')
        print('PASS typed lists/slots/URLs, nested layouts, multi-parameter/catch-all routes, methods, 1 MiB binary multipart, streaming flush and pipeline ordering')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path)
    main(parser.parse_args().out)
