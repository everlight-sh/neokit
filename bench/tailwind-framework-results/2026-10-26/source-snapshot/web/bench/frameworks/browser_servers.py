#!/usr/bin/env python3
"""Serve retained production deployments on fresh origins for browser trials."""
import argparse,json,os,signal,socket,subprocess,time
from pathlib import Path
from adapters import BUILD,ENV,ENGINES


def main():
    p=argparse.ArgumentParser();p.add_argument('--base-port',type=int,required=True);a=p.parse_args()
    processes=[];Path('/tmp/axon-web-browser-servers.pid').write_text(str(os.getpid()))
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        for offset,engine in enumerate(ENGINES):
            port=a.base_port+offset;root=BUILD/'deploy'/engine.lower()
            entry=root/('server' if engine=='Axon' else 'apps/astro/dist-ssr/server/entry.mjs' if engine=='Astro' else 'apps/next/server.js')
            command=[str(entry),str(port)] if engine=='Axon' else ['node',str(entry)]
            env={**ENV,'PORT':str(port),'HOST':'0.0.0.0','HOSTNAME':'0.0.0.0','BENCH_MODE':'ssr'}
            process=subprocess.Popen(['taskset','-c','0',*command],cwd=root,env=env,start_new_session=True)
            processes.append(process)
            for _ in range(300):
                if process.poll() is not None:raise RuntimeError((engine,process.returncode))
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):break
                except OSError:time.sleep(.02)
            else:raise RuntimeError('Server startup timeout')
            print(json.dumps({'engine':engine,'url':f'http://127.0.0.1:{port}/','pid':process.pid}),flush=True)
        print('BROWSER SERVERS READY',flush=True)
        while all(p.poll() is None for p in processes):time.sleep(.2)
    except KeyboardInterrupt:pass
    finally:
        for process in processes:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=15)
        Path('/tmp/axon-web-browser-servers.pid').unlink(missing_ok=True)


if __name__=='__main__':main()
