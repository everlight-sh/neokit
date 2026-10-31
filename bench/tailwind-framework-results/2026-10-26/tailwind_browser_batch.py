#!/usr/bin/env python3
"""Start fresh production origins for one visible-browser measurement batch."""
import argparse,json,subprocess,time,urllib.request


def main():
    p=argparse.ArgumentParser();p.add_argument('--container',required=True);p.add_argument('--base-port',type=int,required=True);p.add_argument('--path',default='/');a=p.parse_args()
    assert 8121<=a.base_port<=8145 and a.path in ('/','/large','/interactive')
    docker=['docker','--context','desktop-linux','exec',a.container]
    subprocess.run([*docker,'python3','-c',"import os,signal,time;from pathlib import Path;p=Path('/tmp/axon-web-browser-servers.pid');os.kill(int(p.read_text()),signal.SIGINT) if p.exists() else None\nfor _ in range(150):\n if not p.exists():break\n time.sleep(.1)\nassert not p.exists(),'Previous browser controller did not stop'"],check=True)
    command=f'exec python3 bench/frameworks/browser_servers.py --base-port {a.base_port} > /results/run/browser-servers-{a.base_port}.log 2>&1'
    subprocess.run(['docker','--context','desktop-linux','exec','-d',a.container,'sh','-c',command],check=True)
    for offset,engine in enumerate(('Axon','Astro','Next')):
        url=f'http://localhost:{a.base_port+offset}{a.path}'
        for _ in range(150):
            try:
                with urllib.request.urlopen(url,timeout=2) as response:
                    assert response.status==200 and b'Framework catalog' in response.read()
                break
            except (OSError,AssertionError):time.sleep(.1)
        else:raise RuntimeError('Browser server did not become ready: '+url)
        print(engine,url,flush=True)


if __name__=='__main__':main()
