"""Build and preview all three showcases; Ctrl-C stops only these servers."""
import argparse,json,os,signal,socket,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
APPS=('ssr-site','blog','saas')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--no-build',action='store_true');parser.add_argument('--base-port',type=int,default=8111);args=parser.parse_args()
    if not 1024<=args.base_port<=65533:parser.error('--base-port must be 1024..65533')
    for index in range(3):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:probe.bind(('127.0.0.1',args.base_port+index))
            except OSError as error:raise SystemExit(f'Preview port {args.base_port+index} is unavailable; use --base-port. {error}') from None
    processes=[];logs=[];directory=ROOT/'build/showcase';directory.mkdir(parents=True,exist_ok=True)
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        info={}
        for index,name in enumerate(APPS):
            if not args.no_build:subprocess.run([str(ROOT/'neokit'),'build','--app',str(ROOT/'examples'/name),'--out',str(directory/name)],cwd=ROOT,check=True)
            binary=directory/name/'server'
            if not binary.exists():raise SystemExit(f'Missing {binary}; run without --no-build first.')
            number=args.base_port+index;log=(directory/f'{name}.log').open('w');logs.append(log)
            process=subprocess.Popen([str(binary),str(number)],env={**os.environ,'NEO_THREADS':'1'},stdout=log,stderr=log,start_new_session=True);processes.append(process)
            deadline=time.monotonic()+15
            while True:
                if process.poll() is not None:raise RuntimeError(f'{name} failed; see {log.name}')
                try:
                    with socket.create_connection(('127.0.0.1',number),timeout=.1):break
                except OSError:
                    if time.monotonic()>deadline:raise RuntimeError(f'{name} readiness timeout')
                    time.sleep(.02)
            info[name]={'url':f'http://127.0.0.1:{number}','pid':process.pid};print(f'{name:10} http://127.0.0.1:{number}',flush=True)
        (directory/'previews.json').write_text(json.dumps(info,indent=2)+'\n')
        print('Native previews are running. Ctrl-C stops all three.',flush=True)
        while all(process.poll() is None for process in processes):time.sleep(.25)
    except KeyboardInterrupt:pass
    finally:
        for process in processes:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
        for process in processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
        for log in logs:log.close()
if __name__=='__main__':main()
