#!/usr/bin/env python3
"""Serve the complete site/assets from a Linux root containing only one binary."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time

from http_smoke import audit,free_port,request


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',type=Path,required=True);args=parser.parse_args()
    chroot=shutil.which('chroot')
    assert os.geteuid()==0 and chroot
    audit(args.binary)
    with tempfile.TemporaryDirectory(prefix='axon-web-empty-root-') as temporary:
        root=Path(temporary);shutil.copy2(args.binary,root/'server');port=free_port()
        process=subprocess.Popen([chroot,str(root),'/server',str(port)],env={'AXON_THREADS':'1'},start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if process.poll() is not None:raise AssertionError(process.stderr.read())
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):break
                except OSError:time.sleep(.03)
            else:raise AssertionError('Empty-root server did not start')
            status,_,html=request(port);assert status==200 and b'LIVE SERVER RENDER' in html
            import re
            urls=[url.decode() for url in re.findall(rb'(?:src|href)="(/_web/[^\"]+)"',html)]
            assert urls and all(request(port,url)[0]==200 for url in urls)
            assert b'Hello, freestanding' in request(port,'/greet?name=freestanding')[2]
            assert request(port,'/api/status')[0]==200
            assert {p.name for p in root.iterdir()}=={'server'}
            print('PASS Linux empty-root SSR, embedded CSS/JS, greeting and API: executable only, no libc/loader/shared libraries/assets/runtime files')
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=10);process.stderr.close()


if __name__=='__main__':main()
