#!/usr/bin/env python3
"""Reproducible uncached SSR and authenticated durable-app comparison.

Build native fixtures first. --prepare-next uses an existing Next installation;
it never installs packages or modifies the supplied node_modules directory.
"""
import argparse
import base64
import contextlib
import hashlib
import hmac
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import platform
import random
import re
import shutil
import signal
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bench.frameworks.model import verify_server

def hardware():
    if sys.platform=='darwin':
        read=lambda name:subprocess.check_output(['sysctl','-n',name],text=True).strip()
        return {'cpu':read('machdep.cpu.brand_string'),'memory_bytes':int(read('hw.memsize')),'logical_cpus':int(read('hw.logicalcpu'))}
    return {'cpu':platform.processor(),'logical_cpus':os.cpu_count()}

def port():
    for _ in range(128):
        number=15000+secrets.randbelow(15000)
        with socket.socket() as sock:
            try:sock.bind(('0.0.0.0',number))
            except OSError:continue
            return number
    raise RuntimeError('No free benchmark listener port')

def b64(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')

def token(key,subject='admin'):
    csrf=b64(b'0123456789abcdef0123456789abcdef')
    body=f'v1.{b64(subject.encode())}.{csrf}.{int(time.time()*1000)+3600000}'
    return body+'.'+b64(hmac.new(key.encode(),body.encode(),hashlib.sha256).digest()),csrf

def request(number,path,headers=None,method='GET',body=None):
    connection=http.client.HTTPConnection('127.0.0.1',number,timeout=20)
    try:
        connection.request(method,path,body.encode('utf-8') if isinstance(body,str) else body,headers or {})
        response=connection.getresponse();return response.status,dict(response.getheaders()),response.read()
    finally:connection.close()

class Tasks(HTMLParser):
    def __init__(self,body):
        super().__init__(convert_charrefs=True);self.rows=[];self.row=None;self.text=None;self.csrf=[];self.feed(body.decode())
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='li' and 'data-task-id' in attrs:self.row=[attrs['data-task-id']]
        if tag=='span' and self.row is not None:self.text=''
        if tag=='input' and attrs.get('name')=='_csrf':self.csrf.append(attrs.get('value'))
    def handle_data(self,value):
        if self.text is not None:self.text+=value
    def handle_endtag(self,tag):
        if tag=='span' and self.text is not None:self.row.append(self.text);self.text=None
        if tag=='li' and self.row is not None:self.rows.append(self.row);self.row=None

def validate_tasks(number,cookie,csrf,origin,directory):
    status,headers,body=request(number,'/dashboard',{'Cookie':cookie,'Accept-Encoding':'identity'})
    assert status==200 and 'no-store' in next(v for k,v in headers.items() if k.lower()=='cache-control'),(status,headers)
    page=Tasks(body);stored=json.loads((directory/'tasks.json').read_text())
    expected=[[t['id'],t['title'],'Done' if t['done'] else 'Open'] for t in stored['tasks'] if t['owner']=='admin']
    assert page.rows==expected and page.csrf and all(v==csrf for v in page.csrf),(len(page.rows),len(expected))
    assert request(number,'/dashboard',{'Cookie':cookie+'tampered'})[0] in (303,307)
    mutation_headers={'Cookie':cookie,'Origin':origin,'X-CSRF-Token':csrf,'Content-Type':'application/json'}
    assert request(number,'/api/tasks/bench-000',{**mutation_headers,'Origin':'https://evil.test'},'PATCH','{"title":"forbidden"}')[0]==403
    assert request(number,'/api/tasks/bench-000',mutation_headers,'PATCH','{"title":"Updated <café> & durable"}')[0]==200
    assert json.loads((directory/'tasks.json').read_text())['tasks'][0]['title']=='Updated <café> & durable'
    return len(body)

def prepare_next(modules,suite):
    app=suite/'apps/next';app.mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/'bench/frameworks/apps/next',app,dirs_exist_ok=True,ignore=shutil.ignore_patterns('node_modules','.next','static-app'))
    shutil.copytree(ROOT/'bench/frameworks/shared',suite/'shared',dirs_exist_ok=True)
    (suite/'package.json').write_text('{"private":true,"type":"module"}')
    if not (suite/'node_modules').exists():(suite/'node_modules').symlink_to(modules,target_is_directory=True)
    (app/'next.config.mjs').write_text('export default {experimental:{cpus:1},poweredByHeader:false,outputFileTracingRoot:'+json.dumps(str(suite))+'};\n')
    for folder,entry in [('dashboard','page.jsx'),('api/tasks/[id]','route.js')]:
        destination=app/'app'/folder;destination.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/'bench/parity-next'/entry,destination/entry)
        shutil.copy2(ROOT/'bench/parity-next/security.mjs',destination/'security.mjs')
    subprocess.run(['node',str(modules/'next/dist/bin/next'),'build',str(app),'--webpack'],env={**os.environ,'NEXT_TELEMETRY_DISABLED':'1','NODE_ENV':'production'},cwd=ROOT,check=True)

@contextlib.contextmanager
def server(command,environment,number,log):
    with log.open('w') as output:
        start=time.monotonic_ns()
        process=subprocess.Popen(command,env={**os.environ,**environment,'NEO_THREADS':'1','NODE_ENV':'production','NEXT_TELEMETRY_DISABLED':'1'},stdout=output,stderr=output,start_new_session=True)
        try:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError(log.read_text())
                try:
                    with socket.create_connection(('127.0.0.1',number),timeout=.05):break
                except OSError:time.sleep(.005)
            else:raise RuntimeError('Server startup timeout')
            yield process,(time.monotonic_ns()-start)/1e6
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=15)

def rss(process):
    lines=subprocess.check_output(['ps','-axo','pgid=,rss='],text=True).splitlines()
    return sum(int(parts[1]) for line in lines if len(parts:=line.split())==2 and int(parts[0])==process.pid)

def latency_ms(value,unit):return float(value)*{'us':.001,'ms':1,'s':1000}[unit]

def wrk(number,script,connections,duration):
    result=subprocess.run(['wrk','-t1','-c'+str(connections),'-d'+str(duration)+'s','--timeout','10s','--latency','-s',str(script),f'http://127.0.0.1:{number}'],capture_output=True,text=True,check=True)
    raw=result.stdout+result.stderr
    errors=re.search(r'Socket errors: connect (\d+), read (\d+), write (\d+), timeout (\d+)',raw)
    bad=re.search(r'Non-2xx or 3xx responses: (\d+)',raw)
    assert not errors or sum(map(int,errors.groups()))==0,raw
    assert not bad or int(bad[1])==0,raw
    values={'requests_per_sec':float(re.search(r'Requests/sec:\s+([\d.]+)',raw)[1]),'raw':raw}
    for percentile in (50,99):
        value=re.search(rf'\s{percentile}%\s+([\d.]+)(us|ms|s)',raw)
        values[f'p{percentile}_ms']=latency_ms(*value.groups())
    return values

def report(data,destination):
    cases=['small-ssr','large-ssr','interactive-ssr','authenticated-db-ssr','durable-mutation']
    frameworks=['baseline-neokit','neokit','next']
    rows=sorted({(s['case'],s['connections'],s['framework']) for s in data['samples']},key=lambda key:(cases.index(key[0]),key[1],frameworks.index(key[2])))
    def metric(case,framework,name,connections=32):
        return statistics.median(s[name] for s in data['samples'] if s['case']==case and s['framework']==framework and s['connections']==connections)
    summary=['| Workload at 32 connections | NeoKit req/s | Next req/s | Throughput ratio | NeoKit vs saved baseline |',
             '|---|---:|---:|---:|---:|']
    for case in cases:
        native=metric(case,'neokit','requests_per_sec');next_=metric(case,'next','requests_per_sec')
        baseline=f'{(native/metric(case,"baseline-neokit","requests_per_sec")-1)*100:+.1f}%' if case in cases[:3] else 'new capability'
        summary.append(f'| {case} | {native:,.1f} | {next_:,.1f} | {native/next_:.1f}× | {baseline} |')
    changes=[(metric(case,'neokit','requests_per_sec')/metric(case,'baseline-neokit','requests_per_sec')-1)*100 for case in cases[:3]]
    lines=['# NeoKit functionality expansion: local production measurements','',
      f"Host: {data['machine']['platform']}, {data['machine'].get('cpu','CPU unspecified')}. Next {data['machine']['next']}, Node {data['machine']['node']}. One server worker/process, one wrk thread, identity encoding; {data['rounds']} shuffled rounds of {data['duration']} seconds after a one-second warmup, with a 10-second client timeout.",'',
      'NeoKit uses release/O3 monolithic builds (`NEOKIT_LIBRARY_CACHE=off`). Next uses its production webpack build with one build worker. The saved pre-expansion baseline is identified by binary hash; these figures compare the complete before/after binaries, not an isolated estimate of each new feature’s cost.','',
      *summary,'',
      f'Catalog throughput changes range from {min(changes):+.1f}% to {max(changes):+.1f}% against the saved baseline at 32 connections. Fully flushed mutations are storage-bound and show a much smaller advantage than SSR. See median and p99 latency separately; no universal latency improvement is implied. Next is the locally installed 16.1.1 production build, not an assertion about the latest release. These are short local trials without formal confidence intervals. Host load and disk headroom are recorded in results.json; storage and background activity make write timings especially provisional.','',
      '| Workload | Connections | Framework | Median req/s | Median p50 ms | Median p99 ms | Median RSS MiB |',
      '|---|---:|---|---:|---:|---:|---:|']
    for case,connections,framework in rows:
        selected=[s for s in data['samples'] if (s['case'],s['connections'],s['framework'])==(case,connections,framework)]
        median=lambda name:statistics.median(s[name] for s in selected)
        lines.append(f'| {case} | {connections} | {framework} | {median("requests_per_sec"):.1f} | {median("p50_ms"):.3f} | {median("p99_ms"):.3f} | {median("rss_kib")/1024:.1f} |')
    lines+=['','| Representative decoded response | NeoKit bytes | Next bytes |','|---|---:|---:|']
    for case in cases[:4]:
        lines.append(f'| {case} | {metric(case,"neokit","response_bytes"):,.0f} | {metric(case,"next","response_bytes"):,.0f} |')
    lines+=['','The fixtures match rendered table/task data and behavior under test, not wire bytes. Next includes its App Router/RSC bootstrap and serialized component data; NeoKit sends HTML with optional widget modules. The throughput comparison includes this architectural payload difference. External browser asset loading is not part of wrk throughput. [Actual-browser functional checks](../../browser-results/2026-11-07-windowed/REPORT.md) are recorded separately. A preliminary Next write trial hit wrk’s default two-second timeout; that partial matrix was discarded and the final matrix uses the same ten-second timeout for every implementation.']
    lines+=['','All timed runs reject socket errors and non-success responses. Independent Python checks compare every catalog cell/checksum/token and fresh timestamps; dashboard checks compare all owner-filtered task rows and CSRF tokens, reject tampered sessions/cross-origin mutations, and verify durable mutation side effects. Requests vary query tokens or mutation titles. Reads are uncached; mutations fully sync the file, rename it, and fully sync its parent directory. On macOS both native syncs request F_FULLFSYNC, matching [Node v25.7.0’s libuv implementation](https://github.com/nodejs/node/blob/v25.7.0/deps/uv/src/unix/fs.c). Earlier trials with weaker native flushes were discarded. Both apps use the same signed-session format, secret, 100 owned tasks plus 20 other-user tasks, and per-user mutation authorization. The Next fixture implements equivalent benchmark endpoints rather than a complete duplicate of the example application.','',
      'RSS is a post-load snapshot summed across the server process group, not peak memory. Startup is socket readiness with 5 ms polling, not cold OS-cache startup. Loopback wrk latency is closed-loop, includes queueing, and has coordinated-omission limitations. This Mac has no CPU affinity control; background load, thermal state, and OS caches are uncontrolled. These results do not establish browser performance, Linux performance, power consumption, OAuth-provider performance, or a universal advantage over Next.js. Baseline binaries precede this functionality expansion; their identities and all raw outputs are recorded in results.json.','']
    destination.write_text('\n'.join(lines))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--next-modules',type=Path,default=ROOT.parent/'makecademy/node_modules')
    parser.add_argument('--prepare-next',action='store_true')
    parser.add_argument('--resume',action='store_true',help='reuse completed samples only when the measured binary identity is unchanged')
    parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--duration',type=int,default=2)
    parser.add_argument('--out',type=Path,default=ROOT/'bench/parity-results/2026-11-07')
    args=parser.parse_args();modules=args.next_modules.resolve();suite=ROOT/'build/performance/next-suite'
    if args.prepare_next:prepare_next(modules,suite)
    args.out.mkdir(parents=True,exist_ok=True)
    frameworks=['baseline-neokit','neokit','next'];cases=[('small-ssr','/?n=3&seed=17&token='),('large-ssr','/large?n=1000&seed=17&token='),('interactive-ssr','/interactive?n=50&seed=17&token='),('authenticated-db-ssr','/dashboard'),('durable-mutation','/api/tasks/bench-000')]
    data={'rounds':args.rounds,'duration':args.duration,'machine':{'platform':platform.platform(),'node':subprocess.check_output(['node','--version'],text=True).strip(),'next':json.loads((modules/'next/package.json').read_text())['version'],'wrk':subprocess.run(['wrk','--version'],capture_output=True,text=True).stdout.strip()},'binaries':{},'samples':[]}
    data['environment']={'disk_free_bytes':shutil.disk_usage(ROOT).free,'load_average':list(os.getloadavg())}
    data['machine'].update(hardware())
    data['machine']['react']=json.loads((modules/'react/package.json').read_text())['version']
    data['machine']['bun']=subprocess.check_output(['bun','--version'],text=True).strip()
    for name,path in [('baseline-neokit',ROOT/'build/performance/baseline/catalog-server'),('neokit',ROOT/'build/catalog/server'),('dashboard',ROOT/'build/dashboard/server')]:
        data['binaries'][name]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    data['next_build_id']=(suite/'apps/next/.next/BUILD_ID').read_text().strip()
    sequence=[(round_,case,path,connections,framework) for round_ in range(args.rounds) for case,path in cases for connections in (1,32) for framework in frameworks if framework!='baseline-neokit' or 'ssr' in case and case!='authenticated-db-ssr']
    # Interleave all implementations and workloads to reduce ordering bias.
    random.Random(729113).shuffle(sequence)
    if args.resume and (args.out/'results.json').is_file():
        previous=json.loads((args.out/'results.json').read_text())
        assert (previous['rounds'],previous['duration'],previous['machine'])==(data['rounds'],data['duration'],data['machine']),'Resume requires the same machine and timing configuration'
        for sample in previous['samples']:
            identity='dashboard' if sample['framework']=='neokit' and sample['case'] in ('authenticated-db-ssr','durable-mutation') else sample['framework']
            unchanged=previous.get('next_build_id',data['next_build_id'])==data['next_build_id'] if identity=='next' else previous['binaries'].get(identity)==data['binaries'].get(identity)
            if unchanged:data['samples'].append(sample)
        complete={(s['round'],s['case'],s['connections'],s['framework']) for s in data['samples']}
        sequence=[s for s in sequence if (s[0],s[1],s[3],s[4]) not in complete]
        print(f'Resuming {len(data["samples"])} validated samples; {len(sequence)} remaining',flush=True)
    key='benchmark-session-key-32-bytes-at-least';signed,csrf=token(key);cookie='neokit_session='+signed
    with tempfile.TemporaryDirectory(prefix='neokit-parity-') as temporary:
        directory=Path(temporary)
        for index,(round_,case,path,connections,framework) in enumerate(sequence):
            number=port();origin=f'http://127.0.0.1:{number}'
            tasks=[{'id':f'bench-{i:03}','owner':'admin' if i<100 else 'other-user','title':f'Task {i}: <café> & "review"','done':i%3==0} for i in range(120)]
            (directory/'tasks.json').write_text(json.dumps({'schema':1,'tasks':tasks},separators=(',',':')))
            environment={'NEOKIT_SESSION_SECRET':key,'NEOKIT_ORIGIN':origin,'NEOKIT_DATA_DIR':str(directory)}
            dashboard=case in ('authenticated-db-ssr','durable-mutation')
            if framework=='next':command=['node',str(modules/'next/dist/bin/next'),'start',str(suite/'apps/next'),'-p',str(number),'-H','127.0.0.1']
            else:command=[str(ROOT/('build/dashboard/server' if dashboard else 'build/catalog/server' if framework=='neokit' else 'build/performance/baseline/catalog-server')),str(number)]
            with server(command,environment,number,directory/'server.log') as (process,startup):
                if dashboard:response_bytes=validate_tasks(number,cookie,csrf,origin,directory)
                else:verify_server(number);response_bytes=len(request(number,path+'validation')[2])
                script=directory/'requests.lua'
                if case=='durable-mutation':
                    code='counter=0\nrequest=function() counter=counter+1; return wrk.format("PATCH",'+json.dumps(path)+', {["Cookie"]='+json.dumps(cookie)+', ["Origin"]='+json.dumps(origin)+', ["X-CSRF-Token"]='+json.dumps(csrf)+', ["Content-Type"]="application/json", ["Accept-Encoding"]="identity"}, "{\\"title\\":\\"Timed update "..counter.."\\"}") end\n'
                elif dashboard:code='wrk.headers["Cookie"]='+json.dumps(cookie)+'\nwrk.headers["Accept-Encoding"]="identity"\nwrk.path="/dashboard"\n'
                else:code='counter=0\nrequest=function() counter=counter+1; return wrk.format("GET",'+json.dumps(path)+'..counter,{["Accept-Encoding"]="identity"}) end\n'
                script.write_text(code)
                wrk(number,script,connections,1)
                measured=wrk(number,script,connections,args.duration)
                measured.update({'round':round_,'framework':framework,'case':case,'connections':connections,'rss_kib':rss(process),'startup_ms':startup,'response_bytes':response_bytes})
                if dashboard:validate_tasks(number,cookie,csrf,origin,directory)
                else:verify_server(number)
                data['samples'].append(measured)
                (args.out/'results.json').write_text(json.dumps(data,indent=2)+'\n')
                print(f'{index+1}/{len(sequence)} {framework} {case} c{connections}: {measured["requests_per_sec"]:.1f} req/s',flush=True)
    report(data,args.out/'REPORT.md')

if __name__=='__main__':main()
