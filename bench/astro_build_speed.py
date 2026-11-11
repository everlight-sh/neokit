#!/usr/bin/env python3
"""Production Axon/Astro builds with explicit compiler-library preparation."""
import argparse,datetime,hashlib,json,os,platform,random,shutil,statistics,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'bench/frameworks'))
from adapters import ENGINES,ENV,BUILD,build_spec,prepare_deployment
from model import SCENARIOS,validate,verify_server
from metrics import Sampler,group,settle
from run_bench import server,wrk

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def med(rows,key):return statistics.median(r[key] for r in rows)
def record(path,row):
 with path.open('a') as f:f.write(json.dumps(row)+'\n')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--rounds',type=int,default=5);p.add_argument('--runtime-rounds',type=int,default=3);p.add_argument('--before-binary',type=Path);a=p.parse_args()
 assert sys.platform=='linux' and a.rounds>=3 and a.runtime_rounds>=3;os.sched_setaffinity(0,{2,3});out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
 for d in ('build-logs','runtime-logs','wrk'):(out/d).mkdir()
 inputs={}
 for base,folders in [(ROOT,('src','compiler','client','bench/frameworks/apps','bench/frameworks/shared')),(Path(os.environ['NEO_HOME']),('neoc','runtime','stdlib'))]:
  for folder in folders:
   for directory,dirs,files in os.walk(base/folder):
    dirs[:]=[d for d in dirs if d not in ('__pycache__','node_modules','.next','.astro','dist-ssr','dist-static','out','.neo-build')]
    for name in files:
     f=Path(directory)/name
     if f.suffix in ('.neo','.py','.js','.jsx','.mjs','.css','.toml','.view'):inputs[str(f)]=sha(f)
 meta={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'platform':platform.uname()._asdict(),'versions':json.loads((ROOT/'bench/frameworks/installed-versions.json').read_text()),'clang':subprocess.check_output(['clang','--version'],text=True),'node':subprocess.check_output(['node','--version'],text=True).strip(),'bun':subprocess.check_output(['bun','--version'],text=True).strip(),'source_sha256':inputs,'build_cpus':[0,1,2,3],'server_cpu':0,'load_cpu':1,'rounds':a.rounds,'runtime_rounds':a.runtime_rounds,'container_ram_gib':4,'compression':'identity','library_preparation':'reported separately; no application native cache retained for cold trials'}
 meta['harness_sha256']=sha(__file__);meta['dependency_lock_sha256']=sha(ROOT/'bench/frameworks/bun.lock')
 if a.before_binary:meta['before_binary_sha256']=sha(a.before_binary)
 (out/'harness.py').write_text(Path(__file__).read_text())
 for file in inputs:
  path=Path(file);prefix='web' if path.is_relative_to(ROOT) else 'axon';base=ROOT if prefix=='web' else Path(os.environ['NEO_HOME']);saved=out/'source-snapshot'/prefix/path.relative_to(base);saved.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,saved)
 (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n');rng=random.Random(26102631);samples=[];runtime=[]
 def run_build(case,argv,cwd,env,repeat,kind,target=None):
  start=time.perf_counter_ns();process=subprocess.Popen(['taskset','-c','0-3',*map(str,argv)],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
  sampler=Sampler(process.pid).start();stdout,_=process.communicate();memory=sampler.stop();wall=time.perf_counter_ns()-start
  (out/'build-logs'/f'{case}-{repeat}-{kind}.log').write_text(stdout);assert process.returncode==0,stdout[-5000:]
  reports=[json.loads(f.read_text()) for f in (target/'generated/.neo-build').glob('*.json')] if target else []
  if target:
   assert len(reports)==1,(case,len(reports))
   assert 'build miss' in stdout if kind!='warm' else 'build miss' not in stdout
   if kind=='cold':assert reports[0]['native_libraries']['cache']=='hit' and 'prechecked_obligations' in reports[0]['native_libraries']
  row={'case':case,'round':repeat,'kind':kind,'wall_ns':wall,**memory,'command':list(map(str,argv)),'compiler':[{k:r[k] for k in ('build','build_stages','optimization','native_libraries','summary') if k in r} for r in reports] if kind!='warm' else []}
  record(out/'build-samples.jsonl',row);samples.append(row);print('BUILD',case,repeat+1,kind,round(wall/1e9,3),flush=True);return row
 with tempfile.TemporaryDirectory(prefix='axon-astro-builds-') as temporary:
  work=Path(temporary);cache=work/'prepared-libraries';env={**ENV,'NEOKIT_LIBRARY_CACHE':str(cache)}
  # Three truly first-use builds, each with both project AND library caches empty.
  for repeat in range(3):
   target=work/('first-use-'+str(repeat));first_env={**ENV,'NEOKIT_LIBRARY_CACHE':str(work/('fresh-library-'+str(repeat)))}
   run_build('Axon-first-use',[ROOT/'neokit','build','--app',ROOT/'bench/frameworks/apps/axon','--out',target],ROOT,first_env,repeat,'first-use',target)
  run_build('Axon-toolchain-prepare',[ROOT/'neokit','prepare'],ROOT,env,0,'prepare')
  artifacts=[]
  for manifest in cache.glob('*/manifest.json'):
   data=json.loads(manifest.read_text());assert all('/bench/frameworks/apps/' not in f for f in data['sources'])
   for file in ('library.o','library.ll'):
    content=(manifest.parent/file).read_bytes();assert b'Framework catalog' not in content and b'Ben & Jerry' not in content,'Application code in dependency cache'
   artifacts.append(dict(data,key=manifest.parent.name,object_bytes=(manifest.parent/'library.o').stat().st_size))
  (out/'prepared-library-artifacts.json').write_text(json.dumps(artifacts,indent=2)+'\n')
  cases=[('Axon-SSR','Axon','ssr','release'),('Astro-SSR','Astro','ssr',None),('Axon-static','Axon','static','release'),('Astro-static','Astro','static',None),('Axon-dev','Axon','ssr','dev')]
  native_paths={}
  for repeat in range(a.rounds):
   order=list(cases);rng.shuffle(order)
   for case,engine,mode,profile in order:
    if engine=='Axon':
     target=work/case;dest=work/(case+'-export');argv=[ROOT/'neokit','export' if mode=='static' else 'build','--app',ROOT/'bench/frameworks/apps/axon','--out',target,'--profile',profile]
     if mode=='static':argv+=['--dest',dest]
     cwd=ROOT;clean=[target,dest];build_env=env
    else:argv,cwd,clean,build_env=build_spec(engine,mode);target=None
    for kind in ('cold','warm'):
     if kind=='cold':
      for f in clean:
       if f.exists():shutil.rmtree(f)
     run_build(case,argv,cwd,build_env,repeat,kind,target)
     if engine=='Axon' and mode=='ssr':
      assert not (target/'render').exists();native_paths[case]=target/'server'
     if mode=='static':
      folder=dest if engine=='Axon' else ROOT/'bench/frameworks/apps/astro/dist-static'
      for s in SCENARIOS:validate((folder/s['path'].lstrip('/')/'index.html').read_bytes(),s['n'],17,'fixture',s['interactive'],static=True)
  BUILD.mkdir(exist_ok=True,parents=True);subprocess.run(['clang','-O2','-shared','-fPIC',str(ROOT/'bench/frameworks/wrk_clock.c'),'-o',str(BUILD/'wrk-clock.so')],check=True)
  deploy={'Axon':{'directory':str(work),'command':[str(native_paths['Axon-SSR'])],'env':ENV,'node':False},'Astro':prepare_deployment('Astro')}
  if a.before_binary:deploy['Axon-before']={'directory':str(work),'command':[str(a.before_binary.resolve())],'env':ENV,'node':False}
  audit={}
  for case in ('Axon-SSR','Axon-dev'):
   binary=native_paths[case];undefined=subprocess.check_output(['nm','-u',str(binary)],text=True);program=subprocess.check_output(['readelf','-l',str(binary)],text=True);dynamic=subprocess.check_output(['readelf','-d',str(binary)],text=True)
   assert not undefined.strip() and 'INTERP' not in program and 'NEEDED' not in dynamic
   audit[case]={'sha256':sha(binary),'bytes':binary.stat().st_size,'undefined':undefined,'headers':program,'dynamic':dynamic}
  (out/'native-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
  checks={}
  for engine,d in {**deploy,'Axon-dev':{'directory':str(work),'command':[str(native_paths['Axon-dev'])],'env':ENV,'node':False}}.items():
   with server(d,out/'runtime-logs'/('verify-'+engine+'.log')) as (_,port,start):checks[engine]=verify_server(port)
  (out/'validation.json').write_text(json.dumps(checks,indent=2)+'\n')
  for repeat in range(a.runtime_rounds):
   for s in SCENARIOS:
    order=list(deploy);rng.shuffle(order)
    for engine in order:
     label=f'{engine}-{s["name"]}-{repeat}'
     with server(deploy[engine],out/'runtime-logs'/(label+'.log')) as (proc,port,start):
      path=s['path']+'?n='+str(s['n'])+'&seed=17&token=compile-benchmark'
      warm=wrk(port,path,32,1);assert not any(warm['errors'].values());settle(proc.pid)
      sampler=Sampler(proc.pid).start();begin=group(proc.pid)['cpu_s'];row=wrk(port,path,32,3);cpu=group(proc.pid)['cpu_s']-begin;memory=sampler.stop()
      (out/'wrk'/(label+'.txt')).write_text(row.pop('raw'));assert row['requests']>0 and not any(row['errors'].values())
      row.update(engine=engine,scenario=s['name'],round=repeat,rps=row['requests']/(row['duration_us']/1e6),cpu_us_per_request=cpu*1e6/row['requests'],**memory);runtime.append(row);record(out/'runtime-samples.jsonl',row)
     print('SSR',label,round(row['rps']),flush=True)
  # Copy a native release binary for optional separate browser checks; it has no runtime files.
  shutil.copy2(native_paths['Axon-SSR'],out/'server-for-browser-check')
 assert all(sha(f)==h for f,h in inputs.items()),'Source changed during measurements'
 lines=['# Axon / Astro build-speed comparison','',f"Native Linux {platform.machine()}, {a.rounds} shuffled cold/warm build rounds. Production uses LLVM -O3. Both frameworks have their build tools/dependencies available. Axon compiler libraries are explicitly prepared once; no application cache is retained for cold project builds.",'','| Build | Clean project seconds | Warm rebuild seconds |','|---|---:|---:|']
 for case,_,_,_ in cases:
  cold=[r for r in samples if r['case']==case and r['kind']=='cold'];warm=[r for r in samples if r['case']==case and r['kind']=='warm'];lines.append(f"| {case} | {med(cold,'wall_ns')/1e9:.3f} | {med(warm,'wall_ns')/1e9:.3f} |")
 first=[r for r in samples if r['case']=='Axon-first-use'];prep=next(r for r in samples if r['case']=='Axon-toolchain-prepare');lines+=['',f"A genuinely first-use Axon production build, with BOTH project and compiler-library caches empty: **{med(first,'wall_ns')/1e9:.3f}s** median ({min(r['wall_ns'] for r in first)/1e9:.3f}–{max(r['wall_ns'] for r in first)/1e9:.3f}s). Framework-only preparation via `./web prepare`: **{prep['wall_ns']/1e9:.3f}s** once. These costs are not hidden in the clean-project table. Astro dependency installation is also outside the table.",'','Cold removes project outputs/native cache, Astro output and its `.astro` cache. Installed toolchain libraries and OS caches remain. Every cold Axon project trial asserts a native application compile miss plus a separately checked dependency-artifact hit. The artifact source list contains runtime/stdlib/framework code; checks reject catalog data in cached IR/object files. Applications are always rechecked and compiled; changing dependency sources/compiler/flags invalidates their artifact. Dev uses -O0 for application code and the prepared -O3 libraries. Both retain source/type/refinement/ownership/effect checks and embedded runtime checks. Linux output remains a single static ELF.','', '| SSR (32 connections) | Framework | Median req/s (min–max) | CPU µs/request | Sampled peak PSS MiB |','|---|---|---:|---:|---:|']
 for s in SCENARIOS:
  for engine in deploy:
   r=[r for r in runtime if r['engine']==engine and r['scenario']==s['name']];lines.append(f"| {s['name']} | {engine} | {med(r,'rps'):,.0f} ({min(x['rps'] for x in r):,.0f}–{max(x['rps'] for x in r):,.0f}) | {med(r,'cpu_us_per_request'):.2f} | {med(r,'peak_pss')/2**20:.2f} |")
 lines+=['',f"{len(runtime)} runtime trials; {a.runtime_rounds} interleaved rounds per cell, 1s warmup + 3s load, one server CPU and separate load CPU, closed-loop HTTP/1.1 with identity encoding. The independent HTML oracle checks every catalog cell, escaping, fresh timestamps and changed query inputs. Zero accepted socket/status/timeout errors. Short local samples are subject to host noise; this is a catalog regression test, not framework feature parity or production capacity. The pre-change native binary is identified separately when supplied. No physical energy claims.",'','See raw JSONL, build/HTTP logs, versions/source hashes, preparation artifacts and static-ELF audits alongside this report. Reproduce with `bench/astro_build_speed.py` in the framework benchmark tools container; frozen JavaScript dependencies must be installed first.','']
 (out/'REPORT.md').write_text('\n'.join(lines));(out/'complete.json').write_text(json.dumps({'complete':True,'build_trials':len(samples),'runtime_trials':len(runtime),'sources_unchanged':True},indent=2)+'\n');print(out/'REPORT.md',flush=True)
if __name__=='__main__':main()
