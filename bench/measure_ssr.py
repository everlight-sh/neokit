#!/usr/bin/env python3
"""Small native SSR smoke measurement, not a cross-framework comparison."""
import argparse
import gzip
import hashlib
import http.client
import json
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from http_smoke import running,audit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'build/studio')
    parser.add_argument('--requests',type=int,default=500)
    parser.add_argument('--report',type=Path,default=ROOT/'build/http-smoke.json')
    args=parser.parse_args()
    if args.requests<20:parser.error('Use at least 20 requests')
    binary=args.out/'server';audit(binary)
    manifest=json.loads((args.out/'manifest.json').read_text())
    samples=[];body_bytes=0
    with running(binary) as port:
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
        for i in range(args.requests+20):
            start=time.perf_counter_ns()
            connection.request('GET','/?name=visitor'+str(i))
            response=connection.getresponse();body=response.read()
            elapsed=time.perf_counter_ns()-start
            assert response.status==200 and ('Hello, visitor'+str(i)).encode() in body
            if i>=20:samples.append(elapsed);body_bytes+=len(body)
        connection.close()
    js=[(url,info) for url,info in manifest['files'].items() if url.endswith('.js')]
    ordered=sorted(samples)
    report={'purpose':'development smoke measurement; localhost, one serial client/worker, no framework comparison',
            'platform':platform.uname()._asdict(),'requests':len(samples),'body_bytes':body_bytes,
            'p50_ms':statistics.median(samples)/1e6,'p95_ms':ordered[int(.95*(len(ordered)-1))]/1e6,
            'serial_requests_per_second':len(samples)/(sum(samples)/1e9),'server_bytes':binary.stat().st_size,
            'server_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'browser_js_bytes':manifest['browser_js_bytes'],
            'browser_js_gzip_bytes_sum':sum(len(gzip.compress((args.out/'assets'/Path(url).name).read_bytes(),mtime=0)) for url,_ in js)}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
