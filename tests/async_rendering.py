"""Real PostgreSQL + delayed HTTP: async pages/APIs, streaming and cancellation.

PostgreSQL is an independent local test service (SCRAM); this harness owns only
its HTTP peers and NeoKit processes. Pass --pg-port for the disposable test DB.
"""
import argparse,concurrent.futures,http.client,json,os,select,signal,socket,subprocess,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class PeerServer(ThreadingHTTPServer):
    request_queue_size=512
    daemon_threads=True
    cancelled=0
    delays={"/auth":.03,"/fast":.08,"/slow":1.2}
    lock=threading.Lock()
class Peer(BaseHTTPRequestHandler):
    def do_GET(self):
        delay=self.server.delays.get(self.path,8)
        deadline=time.monotonic()+delay
        while time.monotonic()<deadline:
            if select.select([self.connection],[],[],min(.02,max(0,deadline-time.monotonic())))[0]:
                if self.connection.recv(1,socket.MSG_PEEK)==b'':
                    with self.server.lock:self.server.cancelled+=1
                    return
        body=('Network <'+self.path[1:]+'> café').encode()
        try:
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass
    def log_message(self,*_):pass

def port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
def request(number,path,method='GET'):
    connection=http.client.HTTPConnection('127.0.0.1',number,timeout=12)
    try:connection.request(method,path);response=connection.getresponse();return response.status,dict(response.getheaders()),response.read()
    finally:connection.close()
def raw(number,path='/async'):
    sock=socket.create_connection(('127.0.0.1',number));sock.settimeout(12);sock.sendall(f'GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode());return sock
def drain(sock,initial=b''):
    data=initial
    while block:=sock.recv(65536):data+=block
    sock.close();return data
def quiet(number):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        stats=json.loads(request(number,'/async-stats')[2])
        if stats['live']==stats['captures']==stats['results']==0:return stats
        time.sleep(.01)
    raise AssertionError(stats)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,default=ROOT/'build/async-features/server');parser.add_argument('--pg-port',type=int,required=True);parser.add_argument('--workers',type=int,default=1);parser.add_argument('--out',type=Path);args=parser.parse_args()
    peer=PeerServer(('127.0.0.1',0),Peer);threading.Thread(target=peer.serve_forever,daemon=True).start();number=port()
    process=subprocess.Popen([str(args.binary.resolve()),str(number)],env={**os.environ,'NEO_THREADS':str(args.workers),'NEOKIT_ASYNC_HTTP_PORT':str(peer.server_port),'NEOKIT_ASYNC_PG_PORT':str(args.pg_port)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,start_new_session=True)
    results={};sockets=[]
    try:
        assert process.stderr.readline().startswith('neo: serving')
        initial=quiet(number);start=time.monotonic();sock=raw(number);data=b'';seen={}
        while block:=sock.recv(65536):
            data+=block;now=time.monotonic()-start
            for name,marker in [('shell',b'Shell ready'),('network',b'Network &lt;fast&gt;'),('database',b'Database &lt;row&gt;'),('slow',b'Network &lt;slow&gt;'),('error',b'data-web-state="error"')]:
                if marker in data and name not in seen:seen[name]=now
        sock.close();assert data.endswith(b'0\r\n\r\n'),data[-300:]
        assert seen['shell']<.3 and seen['network']<seen['database']<seen['slow'] and seen['error']<seen['slow'],seen
        assert b'data-web-stream-end=' in data and b'Database <row>' not in data and b'Content-Length:' not in data[:data.index(b'\r\n\r\n')],data[-400:]
        results['stream_seconds']=seen;print('PASS independent DB/network completion, shell-first framing and isolated timeout',seen,flush=True);quiet(number)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            pending=[pool.submit(request,number,'/async-api') for _ in range(8)]+[pool.submit(request,number,'/async-page')]
            time.sleep(.1);begin=time.monotonic()
            for _ in range(30):assert request(number,'/healthz')[0]==200
            fast=time.monotonic()-begin;assert fast<.3,fast
            for i,future in enumerate(pending):
                status,headers,body=future.result();assert status==200,(status,body)
                assert (b'Network &lt;slow&gt;' if i==8 else b'Network <slow>') in body,body[:200]
                assert headers['Connection']=='close'
        results['fast_during_async_seconds']=fast;print('PASS async pages/APIs yield during network waits; fast requests',fast,flush=True);quiet(number)
        began=time.monotonic();status,headers,body=request(number,'/async-api?target=/timeout');assert status==504 and body==b'Async render expired',(status,body);assert time.monotonic()-began<3;quiet(number)
        # No shell or upstream body can incidentally discover this disconnect.
        # The HTTP worker must observe EOF while all loaders are still waiting.
        before=peer.cancelled
        for _ in range(12):sockets.append(raw(number,'/async-api?target=/hold'))
        until=time.monotonic()+1
        while time.monotonic()<until and json.loads(request(number,'/async-stats')[2])['live']<12:time.sleep(.01)
        for sock in sockets:sock.close()
        sockets.clear();began=time.monotonic();quiet(number);assert time.monotonic()-began<.4
        assert peer.cancelled>before,(before,peer.cancelled)
        before=peer.cancelled
        for _ in range(12):
            sock=raw(number);wire=b''
            while b'Shell ready' not in wire:wire+=sock.recv(4096)
            sockets.append(sock)
        time.sleep(.06)
        for sock in sockets:sock.close()
        sockets.clear();quiet(number);assert peer.cancelled>before,(before,peer.cancelled)
        print('PASS disconnect cancellation releases tasks, captures, results and upstream sockets',flush=True)
        assert request(number,'/async','HEAD')[0]==200;assert request(number,'/async','POST')[0]==405
        status,headers,body=request(number,'/async-api','HEAD');assert status==200 and body==b'';quiet(number)
        results['upstream_cancelled']=peer.cancelled;results['final']=quiet(number);assert results['final']==initial,(initial,results['final'])
        if args.out:args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(results,indent=2)+'\n')
        print('PASS HEAD/method dispatch, buffered deadlines and clean idle resource counts',flush=True)
    finally:
        for sock in sockets:sock.close()
        if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
        try:process.wait(timeout=4)
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait();raise
        peer.shutdown();peer.server_close()
if __name__=='__main__':main()
