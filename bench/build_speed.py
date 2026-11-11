#!/usr/bin/env python3
"""Compare cold/warm production and development builds with the recorded version.
Run on native Linux using bench/frameworks/Dockerfile (no npm install needed).
"""
import argparse,contextlib,datetime,hashlib,json,os,platform,random,shutil,socket,subprocess,sys,tempfile,time,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bench/frameworks'))
from model import SCENARIOS,validate,verify_server
from metrics import Sampler,group,settle

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def median(rows,key):return statistics.median(r[key] for r in rows)
def append(path,row):
 with path.open('a') as file:file.write(json.dumps(row)+'\n')
def fingerprints(root):
 ignored={'node_modules','__pycache__','.git','.axon-build','.neo-build','build','results'}
 return {str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file() and p.suffix in ('.ax','.neo','.py','.view','.toml','.js','.css') and not ignored.intersection(p.relative_to(root).parts)}

@contextlib.contextmanager
def server(binary,log):
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 with log.open('w') as file:
  proc=subprocess.Popen(['taskset','-c','0',str(binary),str(port)],stdout=file,stderr=file,start_new_session=True,env={**os.environ,'NEO_THREADS':'1'})
  try:
   for _ in range(500):
    assert proc.poll() is None,log.read_text()
    try:
     with socket.create_connection(('127.0.0.1',port),timeout=.1):break
    except OSError:time.sleep(.01)
   else:raise AssertionError('Server startup timeout')
   yield proc,port
  finally:
   if proc.poll() is None:proc.terminate()
   proc.wait(timeout=10)

def load(port,path,seconds,clock):
 args=['taskset','-c','1','wrk','-t1','-c32','-d'+str(seconds)+'s','--timeout','10s','-s',str(ROOT/'bench/frameworks/report.lua'),'http://127.0.0.1:'+str(port)+path]
 run=subprocess.run(args,env={**os.environ,'LD_PRELOAD':str(clock)},capture_output=True,text=True,check=True)
 row=json.loads(next(line[6:] for line in run.stdout.splitlines() if line.startswith('BENCH ')))
 return row,run.stdout+run.stderr

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--rounds',type=int,default=3);p.add_argument('--runtime-rounds',type=int,default=5)
 p.add_argument('--before-snapshot',type=Path,default=ROOT/'bench/frameworks/results/2026-10-26/source-snapshot');p.add_argument('--neo-home','--axon-home',dest='axon_home',type=Path,default=Path(os.environ.get('NEO_HOME',ROOT.parent/'axon')))
 a=p.parse_args();assert sys.platform=='linux' and a.rounds>=3 and a.runtime_rounds>=3
 os.sched_setaffinity(0,{2,3});out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
 for name in ('logs','runtime-logs'):(out/name).mkdir()
 meta={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'platform':platform.uname()._asdict(),'clang':subprocess.check_output(['clang','--version'],text=True),'bun':subprocess.check_output(['bun','--version'],text=True).strip(),'rounds':a.rounds,'runtime_rounds':a.runtime_rounds,'build_cpus':[0,1,2,3],'server_cpu':0,'load_cpu':1,'runtime_connections':32,'runtime_seconds':3,'warmup_seconds':1,'after_web_sources':fingerprints(ROOT),'after_axon_sources':fingerprints(a.axon_home)}
 (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
 builds=[];runtime=[];rng=random.Random(26102619)
 with tempfile.TemporaryDirectory(prefix='axon-build-speed-') as temporary:
  work=Path(temporary);old=work/'before';shutil.copytree(a.before_snapshot/'web',old/'axon-web');shutil.copytree(a.before_snapshot/'axon',old/'axon')
  cases={'before-ssr':(old/'axon-web',old/'axon','build',None),'before-static':(old/'axon-web',old/'axon','export',None),'after-ssr':(ROOT,a.axon_home,'build','release'),'after-static':(ROOT,a.axon_home,'export','release'),'after-dev':(ROOT,a.axon_home,'build','dev')}
  paths={name:work/name for name in cases}
  for repeat in range(a.rounds):
   order=list(cases);rng.shuffle(order)
   for name in order:
    web,compiler,command,profile=cases[name];target=paths[name];dest=work/(name+'-export')
    for kind in ('cold','warm'):
     if kind=='cold':
      for folder in (target,dest):
       if folder.exists():shutil.rmtree(folder)
     argv=[sys.executable,str(web/'web'),command,'--app',str(web/'bench/frameworks/apps/axon'),'--out',str(target)]
     if command=='export':argv+=['--dest',str(dest)]
     if profile:argv+=['--profile',profile]
     env={**os.environ,'NEO_HOME':str(compiler),'NEO_THREADS':'1','AXON_HOME':str(compiler),'AXON_THREADS':'1'};env.pop('NEO_OPT_LEVEL',None);env.pop('NEO_INLINE_BUDGET',None)
     start=time.perf_counter_ns();process=subprocess.Popen(['taskset','-c','0-3',*argv],cwd=web,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
     sampler=Sampler(process.pid).start();stdout,_=process.communicate();memory=sampler.stop();elapsed=time.perf_counter_ns()-start
     (out/'logs'/f'{name}-{repeat}-{kind}.log').write_text(stdout);assert process.returncode==0,stdout[-4000:]
     reports=[json.loads(f.read_text()) for f in (target/('generated/.neo-build' if name.startswith('after-') else 'generated/.axon-build')).glob('*.json')]
     row={'case':name,'round':repeat,'kind':kind,'wall_ns':elapsed,'command':argv,**memory,'compiler_reports':[{k:r[k] for k in ('build','build_stages','optimization','summary','binary_sha256') if k in r} for r in reports] if kind=='cold' else []}
     assert 'build miss' not in stdout if kind=='warm' else 'build miss' in stdout
     if name.startswith('after-'):
      assert len(reports)==1,(name,len(reports))
      assert not (target/('server' if command=='export' else 'render')).exists()
     if command=='export':
      for s in SCENARIOS:validate((dest/s['path'].lstrip('/')/'index.html').read_bytes(),s['n'],17,'fixture',s['interactive'],static=True)
     builds.append(row);append(out/'build-samples.jsonl',row);print('BUILD',name,repeat+1,kind,round(elapsed/1e9,3),flush=True)
  clock=work/'wrk-clock.so';subprocess.run(['clang','-O2','-shared','-fPIC',str(ROOT/'bench/frameworks/wrk_clock.c'),'-o',str(clock)],check=True)
  binaries={label:paths[key]/'server' for label,key in [('before','before-ssr'),('after','after-ssr'),('dev','after-dev')]}
  audit={}
  for label,binary in binaries.items():
   undefined=subprocess.check_output(['nm','-u',str(binary)],text=True);program=subprocess.check_output(['readelf','-l',str(binary)],text=True);dynamic=subprocess.check_output(['readelf','-d',str(binary)],text=True)
   assert not undefined.strip() and 'INTERP' not in program and 'NEEDED' not in dynamic
   audit[label]={'sha256':sha(binary),'bytes':binary.stat().st_size,'undefined_symbols':undefined,'program_headers':program,'dynamic':dynamic}
   with server(binary,out/'runtime-logs'/f'verify-{label}.log') as (_,port):assert verify_server(port)==9
  (out/'native-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
  for repeat in range(a.runtime_rounds):
   for s in SCENARIOS:
    order=['before','after'];rng.shuffle(order)
    for label in order:
     key=f'{label}-{s["name"]}-{repeat}'
     with server(binaries[label],out/'runtime-logs'/(key+'.log')) as (proc,port):
      path=s['path']+'?n='+str(s['n'])+'&seed=17&token=build-speed'
      warm,text=load(port,path,1,clock);assert not any(warm['errors'].values()),warm;settle(proc.pid)
      sampler=Sampler(proc.pid).start();begin=group(proc.pid)['cpu_s'];row,text=load(port,path,3,clock);cpu=group(proc.pid)['cpu_s']-begin;memory=sampler.stop()
      (out/'runtime-logs'/(key+'.wrk')).write_text(text);assert row['requests']>0 and not any(row['errors'].values()),row
      row.update(case=label,scenario=s['name'],round=repeat,rps=row['requests']/(row['duration_us']/1e6),cpu_us_per_request=cpu*1e6/row['requests'],**memory);runtime.append(row);append(out/'runtime-samples.jsonl',row)
     print('SSR',key,round(row['rps']),flush=True)
  # Keep the measured executable identities reproducible without storing binaries.
 assert meta['after_web_sources']==fingerprints(ROOT) and meta['after_axon_sources']==fingerprints(a.axon_home),'Sources changed during measurements'
 lines=['# Axon build speed: before / after','',f"Native Linux {platform.machine()}; {a.rounds} build rounds, dependencies already present, cold clears project outputs/caches. Builds use four CPUs; medians include the full CLI. Warm immediately follows cold.",'','| Build | Cold seconds | Warm seconds |','|---|---:|---:|']
 for name in cases:
  cold=[x for x in builds if x['case']==name and x['kind']=='cold'];warm=[x for x in builds if x['case']==name and x['kind']=='warm'];lines.append(f"| {name} | {median(cold,'wall_ns')/1e9:.3f} | {median(warm,'wall_ns')/1e9:.3f} |")
 lines+=['','Production remains LLVM -O3; dev is -O0 with the same type/refinement/bounds/overflow checks. Axon Web uses a forced-inlining budget of 100; general Axon builds retain 160. The old framework built both renderer and server for either command. SSR now builds only the server unless a static route needs prerendering; export builds only its renderer. The independent oracle checks all exported rows and all three live servers, including dev.','',f'Runtime check: {a.runtime_rounds} interleaved rounds, one server CPU, 32 connections, 3s load after 1s warmup. Closed-loop microbenchmark; ranges expose host noise.','', '| SSR | Before req/s (min–max) | After req/s (min–max) | Before / after CPU µs/request |','|---|---:|---:|---:|']
 for s in SCENARIOS:
  rows={label:[r for r in runtime if r['case']==label and r['scenario']==s['name']] for label in ('before','after')}
  cells=[f"{median(rows[l],'rps'):,.0f} ({min(x['rps'] for x in rows[l]):,.0f}–{max(x['rps'] for x in rows[l]):,.0f})" for l in ('before','after')]
  lines.append(f"| {s['name']} | {cells[0]} | {cells[1]} | {median(rows['before'],'cpu_us_per_request'):.2f} / {median(rows['after'],'cpu_us_per_request'):.2f} |")
 lines+=['','All accepted runtime samples have zero socket/status/timeout errors. Raw logs, compiler phase reports, source hashes and dependency audits are retained. This is build/request performance for this catalog, not full framework parity. Dependency installation and OS cache flushing are excluded.','']
 (out/'REPORT.md').write_text('\n'.join(lines));(out/'complete.json').write_text(json.dumps({'complete':True,'build_trials':len(builds),'runtime_trials':len(runtime),'source_hashes_unchanged':True},indent=2)+'\n');print(out/'REPORT.md',flush=True)
if __name__=='__main__':main()
