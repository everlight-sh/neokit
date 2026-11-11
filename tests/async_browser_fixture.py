"""Owns only local verification peers and a NeoKit server; terminate to clean up."""
import argparse,json,os,signal,subprocess,threading,time
from pathlib import Path
from async_rendering import PeerServer,Peer,port
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',type=Path,default=ROOT/'build/async-features/server');parser.add_argument('--pg-port',type=int,required=True);args=parser.parse_args()
    peer=PeerServer(('127.0.0.1',0),Peer);peer.delays={'/auth':.1,'/fast':1.2,'/slow':6,'/timeout':8};threading.Thread(target=peer.serve_forever,daemon=True).start();number=port()
    directory=ROOT/'build/async-browser';directory.mkdir(exist_ok=True)
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    with (directory/'server.log').open('w') as log:
        process=subprocess.Popen([str(args.binary.resolve()),str(number)],env={**os.environ,'NEO_THREADS':'1','NEOKIT_ASYNC_HTTP_PORT':str(peer.server_port),'NEOKIT_ASYNC_PG_PORT':str(args.pg_port),'NEOKIT_ASYNC_PG_DELAY':'4'},stdout=log,stderr=log,start_new_session=True)
        try:
            info={'port':number,'http_peer':peer.server_port,'server_pid':process.pid};(directory/'ports.json').write_text(json.dumps(info));print(json.dumps(info),flush=True)
            while process.poll() is None:time.sleep(.2)
        except KeyboardInterrupt:pass
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=4)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            peer.shutdown();peer.server_close()
if __name__=='__main__':main()
