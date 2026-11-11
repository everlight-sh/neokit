#!/usr/bin/env python3
"""Compare prepared-library and monolithic release binaries on the same fixtures.

Build the prepared variants under build/cache-check/{catalog,dashboard} first.
The broader Next/baseline comparison is parity_performance.py.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bench.parity_performance import ROOT,hardware,port,token,server,rss,wrk,verify_server,validate_tasks

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'bench/parity-results/2026-11-07')
    parser.add_argument('--prepared-dir',type=Path,default=ROOT/'build/cache-check')
    parser.add_argument('--cases',nargs='+',choices=['small-ssr','large-ssr','interactive-ssr','authenticated-db-ssr'])
    args=parser.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
    cases=[('small-ssr','/?n=3&seed=17&token='),('large-ssr','/large?n=1000&seed=17&token='),('interactive-ssr','/interactive?n=50&seed=17&token='),('authenticated-db-ssr','/dashboard')]
    if args.cases:cases=[case for case in cases if case[0] in args.cases]
    sequence=[(round_,case,path,mode) for round_ in range(3) for case,path in cases for mode in ('prepared','monolithic')]
    random.Random(132924).shuffle(sequence)
    result={'configuration':{'rounds':3,'duration_s':2,'warmup_s':1,'connections':32,'workers':1,'client_timeout_s':10},'hardware':hardware(),'binaries':{},'samples':[]}
    key='profile-test-session-key-at-least-32-bytes';signed,csrf=token(key);cookie='neokit_session='+signed
    with tempfile.TemporaryDirectory(prefix='neokit-cache-profile-') as temporary:
        directory=Path(temporary)
        for index,(round_,case,path,mode) in enumerate(sequence):
            number=port();dashboard=case=='authenticated-db-ssr';name='dashboard' if dashboard else 'catalog'
            binary=(args.prepared_dir/name if mode=='prepared' else ROOT/'build'/name)/'server'
            result['binaries'][mode+'-'+name]={'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'bytes':binary.stat().st_size}
            tasks=[{'id':f'bench-{i:03}','owner':'admin' if i<100 else 'other-user','title':f'Task {i}: <café> & "review"','done':i%3==0} for i in range(120)]
            (directory/'tasks.json').write_text(json.dumps({'schema':1,'tasks':tasks},separators=(',',':')))
            origin=f'http://127.0.0.1:{number}';environment={'NEOKIT_DATA_DIR':str(directory),'NEOKIT_SESSION_SECRET':key,'NEOKIT_ORIGIN':origin}
            with server([str(binary),str(number)],environment,number,directory/'server.log') as (process,startup):
                if dashboard:validate_tasks(number,cookie,csrf,origin,directory)
                else:verify_server(number)
                script=directory/'requests.lua'
                code='wrk.headers["Cookie"]='+json.dumps(cookie)+'\nwrk.headers["Accept-Encoding"]="identity"\nwrk.path="/dashboard"\n' if dashboard else 'counter=0\nrequest=function() counter=counter+1; return wrk.format("GET",'+json.dumps(path)+'..counter,{["Accept-Encoding"]="identity"}) end\n'
                script.write_text(code);wrk(number,script,32,1);sample=wrk(number,script,32,2)
                sample.update(round=round_,case=case,mode=mode,rss_kib=rss(process),startup_ms=startup)
                if dashboard:validate_tasks(number,cookie,csrf,origin,directory)
                else:verify_server(number)
                result['samples'].append(sample)
                (out/'cache-profile.json').write_text(json.dumps(result,indent=2)+'\n')
                print(f'{index+1}/{len(sequence)} {mode} {case}: {sample["requests_per_sec"]:.1f} req/s',flush=True)
    lines=['# Prepared-library build profile check','',
        'Measured binaries pass the independent semantic checks from the main benchmark. This supplement compares their server throughput: three shuffled two-second trials after a one-second warmup, 32 connections, one worker and one wrk thread, identity encoding and a ten-second client timeout. Dashboard builds also pass the login/authorization/durable-write integration checks recorded with the verification report.','',
        '| Workload | Prepared req/s | Monolithic req/s | Prepared / monolithic |','|---|---:|---:|---:|']
    for case,_ in cases:
        values={mode:statistics.median(s['requests_per_sec'] for s in result['samples'] if s['case']==case and s['mode']==mode) for mode in ('prepared','monolithic')}
        lines.append(f'| {case} | {values["prepared"]:,.1f} | {values["monolithic"]:,.1f} | {values["prepared"]/values["monolithic"]:.2f}× |')
    lines+=['','Short local trials and near-full-disk/host-load limitations still apply; this is not a formal confidence interval or a browser benchmark. Both modes retain Neo checks. Prepared libraries are the normal cache-enabled build path; monolithic uses NEOKIT_LIBRARY_CACHE=off. Raw outputs and binary identities are in [cache-profile.json](cache-profile.json).','']
    (out/'CACHE-PROFILE.md').write_text('\n'.join(lines))

if __name__=='__main__':main()
