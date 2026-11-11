#!/usr/bin/env python3
"""Compare the Fieldnotes app before/after Tailwind on the local host.

Pass a pre-change framework checkout via --before-root. Toolchain/dependency
installation and checked framework-library preparation are outside build timing.
"""
import argparse
import contextlib
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.http_smoke import audit, request, running


def sha(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def median(rows, key):return statistics.median(r[key] for r in rows)
def append(file, row):
    with file.open('a') as stream:stream.write(json.dumps(row)+'\n')


def snapshot(root, dest):
    hashes={}
    for folder in ('compiler','client','src','examples/studio'):
        for file in sorted((root/folder).rglob('*')):
            if not file.is_file() or any(p in ('__pycache__','.neo-build','node_modules') for p in file.parts):continue
            relative=file.relative_to(root);target=dest/relative
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
            hashes[str(relative)]=sha(file)
    for name in ('web','package.json','bun.lock'):
        file=root/name
        if file.is_file():
            dest.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,dest/name);hashes[name]=sha(file)
    return hashes


def load(port, path, duration):
    proc=subprocess.run(['wrk','-t2','-c16','-d'+str(duration)+'s','--latency',f'http://127.0.0.1:{port}{path}'],capture_output=True,text=True,check=True)
    raw=proc.stdout+proc.stderr
    if 'Socket errors:' in raw or 'Non-2xx or 3xx responses:' in raw:raise AssertionError(raw)
    match=re.search(r'(\d+) requests in ([0-9.]+)([smh])',raw)
    if not match:raise AssertionError(raw)
    count=int(match[1]);seconds=float(match[2])*{'s':1,'m':60,'h':3600}[match[3]]
    p99=re.search(r'^\s*99%\s+([0-9.]+)(us|ms|s)',raw,re.M)
    assert count>0 and p99,raw
    return {'requests':count,'wall_s':seconds,'rps':count/seconds,
            'p99_ms':float(p99[1])*{'us':.001,'ms':1,'s':1000}[p99[2]],'errors':0}, raw


class RSS:
    def __init__(self,pid):self.pid=pid;self.samples=[];self.stop_event=threading.Event()
    def __enter__(self):
        def sample():
            while not self.stop_event.is_set():
                result=subprocess.run(['ps','-o','rss=','-p',str(self.pid)],capture_output=True,text=True)
                if result.returncode==0 and result.stdout.strip():self.samples.append(int(result.stdout.strip())*1024)
                self.stop_event.wait(.15)
        self.thread=threading.Thread(target=sample,daemon=True);self.thread.start();return self
    def __exit__(self,*_):self.stop_event.set();self.thread.join()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--build-rounds',type=int,default=5);p.add_argument('--runtime-rounds',type=int,default=3)
    a=p.parse_args();assert a.build_rounds>=3 and a.runtime_rounds>=3
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);(out/'logs').mkdir()
    roots={'Before':a.before_root.resolve(),'Tailwind':ROOT}
    compiler=Path(os.environ.get('NEO_HOME',ROOT.parent/'neo')).resolve()
    env={**os.environ,'NEO_HOME':str(compiler),'NEO_THREADS':'1'}
    builds=[];runtime=[];outputs={label:out/('build-'+label.lower()) for label in roots}
    hashes={label:snapshot(root,out/'source-snapshot'/label.lower()) for label,root in roots.items()}
    meta={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'platform':platform.platform(),
          'machine':platform.machine(),'bun':subprocess.check_output(['bun','--version'],text=True).strip(),
          'tailwind':json.loads((ROOT/'node_modules/tailwindcss/package.json').read_text())['version'],
          'source_sha256':hashes,'compiler_sha256':{str(f.relative_to(compiler)):sha(f) for f in sorted((compiler/'neoc').glob('*.py'))},
          'build_rounds':a.build_rounds,'runtime_rounds':a.runtime_rounds,'server_workers':1,'connections':16,
          'duration_s':3,'warmup_s':1,'compression':'identity','build_cache':'cold deletes all project outputs; dependencies/framework objects are prepared outside timing',
          'harness_sha256':sha(Path(__file__))}
    if sys.platform=='darwin':meta['cpu']=subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'],text=True).strip()
    (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n');shutil.copyfile(__file__,out/'harness.py')
    def build(label, round_, kind):
        target=outputs[label];root=roots[label]
        if kind=='cold' and target.exists():shutil.rmtree(target)
        start=time.perf_counter()
        proc=subprocess.run([str(root/'web'),'build','--out',str(target)],cwd=root,env=env,capture_output=True,text=True)
        elapsed=time.perf_counter()-start;log=proc.stdout+proc.stderr
        (out/'logs'/f'build-{label}-{round_}-{kind}.log').write_text(log)
        if proc.returncode:raise AssertionError(log)
        if kind=='cold':assert 'build miss' in log,log
        css_time=re.search(r'Tailwind [^:]+: site.css, \d+ bytes, (\d+)ms',log)
        row={'case':label,'round':round_,'kind':kind,'wall_s':elapsed,'css_compile_ms':int(css_time[1]) if css_time else 0}
        if kind!='prepare':builds.append(row);append(out/'build-samples.jsonl',row)
        print('BUILD',label,kind,round(elapsed,3),flush=True)
    for label in roots:build(label,-1,'prepare')
    rng=random.Random(261026)
    for i in range(a.build_rounds):
        order=list(roots);rng.shuffle(order)
        for label in order:
            for kind in ('cold','warm'):build(label,i,kind)
    assets={}
    for label,target in outputs.items():
        audit(target/'server')
        manifest=json.loads((target/'manifest.json').read_text())
        css=b''.join((target/'assets'/Path(url).name).read_bytes() for url in manifest['files'] if url.endswith('.css'))
        js=b''.join((target/'assets'/Path(url).name).read_bytes() for url in manifest['files'] if url.endswith('.js'))
        assets[label]={'css_bytes':len(css),'css_gzip_bytes':len(gzip.compress(css,mtime=0)),'browser_js_bytes':len(js),'binary_bytes':(target/'server').stat().st_size,'binary_sha256':sha(target/'server')}
        with running(target/'server') as port:
            status,_,body=request(port);assert status==200 and b'A smaller' in body
            assets[label]['home_html_bytes']=len(body)
            for url in manifest['files']:assert request(port,url)[0]==200
            if label=='Tailwind':
                assert b'min-[761px]:grid-cols-2' in body and b'.min-\\[761px\\]\\:grid-cols-2' in css
                assert b'@import' not in css
    for i in range(a.runtime_rounds):
        for scenario,path in [('home','/'),('greeting','/greet?name=Tailwind%20%26%20Axon')]:
            order=list(roots);rng.shuffle(order)
            for label in order:
                with running(outputs[label]/'server') as port:
                    # Discover the single direct child server owned by running().
                    status,_,body=request(port,path);assert status==200
                    if scenario=='greeting':assert b'Tailwind &amp; Axon' in body
                    load(port,path,1)
                    listing=subprocess.check_output(['pgrep','-P',str(os.getpid())],text=True)
                    pid=next(int(pid) for pid in listing.split() if str(outputs[label]/'server') in subprocess.check_output(['ps','-o','command=','-p',pid],text=True))
                    with RSS(pid) as memory:row,raw=load(port,path,3)
                    row.update(case=label,scenario=scenario,round=i,sampled_peak_rss_bytes=max(memory.samples,default=0))
                    assert row['sampled_peak_rss_bytes']>0
                    runtime.append(row);append(out/'runtime-samples.jsonl',row)
                    (out/'logs'/f'http-{label}-{scenario}-{i}.txt').write_text(raw)
                    print('HTTP',label,scenario,round(row['rps']),flush=True)
    assert all(sha(root/rel)==digest for label,root in roots.items() for rel,digest in hashes[label].items()),'Source changed'
    (out/'assets.json').write_text(json.dumps(assets,indent=2)+'\n')
    lines=['# Fieldnotes Tailwind integration performance','',f'{meta.get("cpu",meta["machine"])} / {meta["platform"]}; Bun {meta["bun"]}, Tailwind {meta["tailwind"]}. Same native Axon compiler, release optimization, one server worker. Before is the original plain-CSS Fieldnotes snapshot.','',
           'The original framework libraries and the updated framework libraries are prepared outside timing; dependencies are already installed. Cold deletes project outputs and native application cache. The unchanged macOS compiler still uses its OS loader; separate Linux scratch execution verifies the C-free deployment. These are local before/after measurements, not a new Astro comparison.','',
           '| Build | Before | Tailwind |','|---|---:|---:|']
    for kind in ('cold','warm'):lines.append('| '+kind+' | '+' | '.join(f'{median([r for r in builds if r["case"]==label and r["kind"]==kind],"wall_s"):.3f}s' for label in roots)+' |')
    lines+=['','| Assets | Before | Tailwind |','|---|---:|---:|']
    for key in ('css_bytes','css_gzip_bytes','browser_js_bytes','home_html_bytes','binary_bytes'):lines.append('| '+key+' | '+' | '.join(f'{assets[label][key]:,}' for label in roots)+' |')
    lines+=['','Gzip sizes are offline estimates; the current server sends identity CSS. Tailwind ships no browser JavaScript.','', '| SSR | Version | Median req/s (range) | p99 ms | Sampled peak RSS MiB |','|---|---|---:|---:|---:|']
    for scenario in ('home','greeting'):
        for label in roots:
            rows=[r for r in runtime if r['case']==label and r['scenario']==scenario]
            lines.append(f'| {scenario} | {label} | {median(rows,"rps"):,.0f} ({min(r["rps"] for r in rows):,.0f}–{max(r["rps"] for r in rows):,.0f}) | {median(rows,"p99_ms"):.2f} | {median(rows,"sampled_peak_rss_bytes")/2**20:.2f} |')
    lines+=['',f'{len(builds)} timed builds, {len(runtime)} load trials, zero accepted HTTP/socket errors. {a.runtime_rounds} shuffled rounds per route/version, 16 keepalive connections, 1s warmup + 3s load. RSS sampled every 150ms; this is not a full browser heap or energy measurement. Host scheduling and memory pressure affect short local samples. Static export, browser interactions, responsive CSS and Linux empty-root execution are checked separately.','',
            'Reproduce: `python3 bench/tailwind.py --before-root /path/to/pre-tailwind/axon-web --out build/tailwind-rerun`. Keep the baseline checkout unchanged and install current build dependencies with `bun install --frozen-lockfile`. Source snapshots/hashes and raw logs accompany this report.','']
    (out/'REPORT.md').write_text('\n'.join(lines))
    (out/'complete.json').write_text(json.dumps({'complete':True,'build_trials':len(builds),'runtime_trials':len(runtime),'sources_unchanged':True},indent=2)+'\n')
    print(out/'REPORT.md')


if __name__=='__main__':main()
