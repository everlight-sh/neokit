#!/usr/bin/env python3
"""Native Linux production SSR/SSG comparison with independent HTML verification."""
import argparse
import contextlib
import datetime
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import signal
import socket
import statistics
import subprocess
import tempfile
import time
from urllib.parse import urlencode,urljoin,urlparse

from adapters import ROOT,HERE,BUILD,ENGINES,ENV,build_spec,prepare_deployment,start_spec,export_directory
from metrics import Sampler,group,settle
from model import Catalog,SCENARIOS,get,validate,verify_server


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def command(args,**kwargs):return subprocess.check_output(list(map(str,args)),text=True,**kwargs).strip()


def sources():
    paths={}
    suffixes={'.py','.ax','.js','.jsx','.mjs','.astro','.view','.toml','.json','.css','.lock','.lua','.c'}
    ignored={'.git','node_modules','__pycache__','.axon-build','.next','.astro','dist-ssr','dist-static','out','build'}
    for prefix,base,folders in [('web',ROOT,('src','compiler','client','bench/frameworks')),('axon',Path(os.environ['AXON_HOME']),('axonc','runtime','stdlib'))]:
        for folder in folders:
            for path in (base/folder).rglob('*'):
                rel=path.relative_to(base)
                if path.is_file() and not any(p in ignored for p in rel.parts) and (path.suffix in suffixes or path.name=='Dockerfile'):
                    paths[prefix+'/'+rel.as_posix()]=path
    for path in [ROOT/'web',ROOT/'axon.toml',HERE/'bun.lock']:
        paths['web/'+path.relative_to(ROOT).as_posix()]=path
    return paths


def size(directory):
    files=[p for p in Path(directory).rglob('*') if p.is_file()]
    return {'files':len(files),'bytes':sum(p.stat().st_size for p in files)}


def append(path,value):
    with path.open('a') as file:file.write(json.dumps(value)+'\n')


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


@contextlib.contextmanager
def server(deployment,log):
    port=free_port();argv,env=start_spec(deployment,port)
    start=time.perf_counter_ns()
    with log.open('w') as output:
        process=subprocess.Popen(['taskset','-c','0',*argv],cwd=deployment['directory'],env=env,stdout=output,stderr=output,start_new_session=True)
        try:
            for _ in range(600):
                if process.poll() is not None:raise AssertionError((argv,process.returncode,log.read_text()[-3000:]))
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):break
                except OSError:time.sleep(.01)
            else:raise AssertionError('Startup timeout: '+str(argv))
            ready=time.perf_counter_ns()-start
            yield process,port,ready
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=15)


def build_trial(engine,mode,kind,round_id,out):
    argv,cwd,clean,env=build_spec(engine,mode)
    if kind=='cold':
        for path in clean:
            if path.exists():shutil.rmtree(path)
    label=f'{mode}-{round_id+1}-{engine}-{kind}'
    before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.perf_counter_ns()
    process=subprocess.Popen(['taskset','-c','0-3',*argv],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
    sampler=Sampler(process.pid).start();stdout,_=process.communicate();memory=sampler.stop()
    elapsed=time.perf_counter_ns()-start;after=resource.getrusage(resource.RUSAGE_CHILDREN)
    (out/'build-logs'/f'{label}.log').write_text(stdout)
    result={'engine':engine,'mode':mode,'kind':kind,'round':round_id,'elapsed_ns':elapsed,
            'cpu_ns':round((after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime)*1e9),**memory,'command':argv,'returncode':process.returncode}
    append(out/'build-samples.jsonl',result)
    assert process.returncode==0,(label,stdout[-4000:])
    print(f'BUILD {label}: {elapsed/1e9:.3f}s, {memory["peak_pss"]/2**20:.1f} MiB sampled peak PSS',flush=True)
    return result


def browser_payload(port,scenario):
    status,headers,body,_,_=get(port,scenario['path'])
    assert status==200
    page=validate(body,scenario['n'],17,'fixture',scenario['interactive'])
    assets={};css={}
    for collection,urls in [(assets,page.scripts),(css,page.styles)]:
        for url in sorted(set(urls)):
            parsed=urlparse(url)
            assert not parsed.netloc,'External asset in fixture: '+url
            path=urljoin(scenario['path'],url)
            status,_,data,_,_=get(port,path);assert status==200,(url,status)
            collection[path]={'bytes':len(data),'gzip_bytes':len(gzip.compress(data,mtime=0)),'sha256':hashlib.sha256(data).hexdigest()}
    inline=b''.join(page.inline_scripts)
    return {'html_bytes':len(body),'html_gzip_bytes':len(gzip.compress(body,mtime=0)),
            'html_referenced_external_js_bytes':sum(a['bytes'] for a in assets.values()),
            'html_referenced_external_js_gzip_bytes':sum(a['gzip_bytes'] for a in assets.values()),
            'inline_script_bytes':len(inline),'inline_script_gzip_bytes':len(gzip.compress(inline,mtime=0)) if inline else 0,
            'js_assets':assets,'css_assets':css,'note':'HTML-declared script/preload resources and inline scripts; not a browser network trace or INP measurement'}


def static_metrics(engine):
    folder=export_directory(engine)
    assert folder.is_dir(),folder
    pages={}
    for scenario in SCENARIOS:
        path=folder/scenario['path'].lstrip('/')/'index.html'
        if not path.exists():path=folder/(scenario['path'].lstrip('/')+'.html')
        body=path.read_bytes();page=validate(body,scenario['n'],17,'fixture',scenario['interactive'],static=True)
        pages[scenario['path']]={'html_bytes':len(body),'inline_script_bytes':sum(len(x) for x in page.inline_scripts),'external_script_urls':sorted(set(page.scripts))}
    return {**size(folder),'client_js_bytes':sum(p.stat().st_size for p in folder.rglob('*.js')),'html_bytes':sum(p.stat().st_size for p in folder.rglob('*.html')),'pages':pages}


def wrk(port,path,connections,duration):
    argv=['taskset','-c','1','wrk','-t1','-c'+str(connections),'-d'+str(duration)+'s','--timeout','10s','-s',str(HERE/'report.lua'),'http://127.0.0.1:'+str(port)+path]
    before=resource.getrusage(resource.RUSAGE_CHILDREN)
    result=subprocess.run(argv,capture_output=True,text=True,check=True,env={**os.environ,'LD_PRELOAD':str(BUILD/'wrk-clock.so')})
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    lines=[line[len('BENCH '):] for line in result.stdout.splitlines() if line.startswith('BENCH ')]
    assert len(lines)==1,result.stdout+result.stderr
    sample=json.loads(lines[0]);sample['command']=argv
    sample['load_cpu_ns']=round((after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime)*1e9)
    sample['raw']=result.stdout+'\n'+result.stderr
    return sample


def trial(engine,deployment,scenario,connections,round_id,args,out):
    label=f'{scenario["name"]}-c{connections}-r{round_id+1}-{engine}'
    query={'n':scenario['n'],'seed':17,'token':'measured-'+label}
    path=scenario['path']+'?'+urlencode(query)
    with server(deployment,out/'runtime-logs'/f'{label}.log') as (process,port,startup):
        status,_,body,_,_=get(port,path);assert status==200
        validate(body,scenario['n'],17,query['token'],scenario['interactive'])
        warm=wrk(port,path,connections,args.warmup)
        (out/'wrk'/f'{label}-warmup.txt').write_text(warm.pop('raw'))
        assert warm['requests']>0 and not any(warm['errors'].values()),warm
        settle(process.pid)
        idle=group(process.pid);sampler=Sampler(process.pid).start()
        begin_cpu=group(process.pid)['cpu_s']
        result=wrk(port,path,connections,args.duration)
        cpu_ns=round(max(0,group(process.pid)['cpu_s']-begin_cpu)*1e9);memory=sampler.stop()
        (out/'wrk'/f'{label}.txt').write_text(result.pop('raw'))
        if not result['requests'] or any(result['errors'].values()):
            (out/'failed-trial.json').write_text(json.dumps(result,indent=2)+'\n')
            raise AssertionError(result)
        (out/'samples'/f'{label}-memory.json').write_text(json.dumps(sampler.samples))
        status,_,body,_,_=get(port,path);assert status==200
        validate(body,scenario['n'],17,query['token'],scenario['interactive'])
        serial=[]
        for _ in range(5):
            status,_,body,ttfb,total=get(port,path);assert status==200
            validate(body,scenario['n'],17,query['token'],scenario['interactive']);serial.append({'ttfb_ns':ttfb,'complete_ns':total})
    result.update(engine=engine,scenario=scenario['name'],n=scenario['n'],connections=connections,round=round_id,
                  rps=result['requests']/(result['duration_us']/1e6),cpu_ns=cpu_ns,cpu_us_per_request=cpu_ns/result['requests']/1000,
                  startup_ns=startup,idle_rss=idle['rss'],idle_pss=idle['pss'],**memory,serial=serial)
    append(out/'samples.jsonl',result)
    print(f'SSR {label}: {result["rps"]:.1f} req/s, p95 {result["p95_us"]/1000:.2f}ms, CPU {result["cpu_us_per_request"]:.1f}us/req, PSS {memory["peak_pss"]/2**20:.1f}MiB',flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--rounds',type=int,default=5)
    parser.add_argument('--build-rounds',type=int,default=3)
    parser.add_argument('--duration',type=float,default=4)
    parser.add_argument('--warmup',type=float,default=1)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--reuse-builds',type=Path,help='Reuse a complete set of build trials only if all build inputs are unchanged')
    parser.add_argument('--tools-image',default='unknown')
    args=parser.parse_args()
    assert platform.system()=='Linux' and platform.machine() in ('aarch64','x86_64') and not os.environ.get('AXON_RUNNER')
    assert args.rounds>=3 or args.self_test
    assert args.build_rounds>=1 and args.duration>=1 and args.warmup>=.5
    os.sched_setaffinity(0,{2,3})
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    for name in ('build-logs','runtime-logs','wrk','samples','source-snapshot'):(out/name).mkdir()
    BUILD.mkdir(parents=True,exist_ok=True)
    subprocess.run(['clang','-O2','-shared','-fPIC',str(HERE/'wrk_clock.c'),'-o',str(BUILD/'wrk-clock.so')],check=True)
    rng=random.Random(261026391)
    versions=json.loads((HERE/'installed-versions.json').read_text())
    expected=json.loads((HERE/'package.json').read_text())['dependencies'];assert versions==expected,(versions,expected)
    paths=sources();hashes={name:sha(path) for name,path in paths.items()}
    for name,path in paths.items():
        target=out/'source-snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    metadata={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'platform':platform.uname()._asdict(),
              'versions':versions,'node':command(['node','--version']),'bun':command(['bun','--version']),'clang':command(['clang','--version']),
              'node_bytes':Path(shutil.which('node')).stat().st_size,'node_sha256':sha(shutil.which('node')),'tools_image':args.tools_image,
              'cpuinfo':Path('/proc/cpuinfo').read_text(),'meminfo':Path('/proc/meminfo').read_text(),
              'load_clock':'CLOCK_MONOTONIC via benchmark-only wrk LD_PRELOAD; never applied to servers','wrk_package':command(['dpkg-query','-W','-f=${Version}','wrk']),'settings':{'server_cpu':0,'load_cpu':1,'driver_cpus':[2,3],'build_cpus':[0,1,2,3],'rounds':args.rounds,'build_rounds':args.build_rounds,'duration_s':args.duration,'warmup_s':args.warmup,'connections':[1,32],'seed':261026391,'NODE_ENV':'production','AXON_THREADS':1,'compression':'Accept-Encoding identity; wrk default sends no Accept-Encoding','streaming':'framework defaults retained; latency covers complete responses'},'sources':hashes}
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    builds=[]
    if args.reuse_builds:
        previous=args.reuse_builds.resolve();old=json.loads((previous/'metadata.json').read_text())
        allowed={'web/bench/frameworks/run_bench.py','web/bench/frameworks/render_report.py','web/bench/frameworks/wrk_clock.c'}
        changed={k for k in hashes.keys()|old['sources'].keys() if hashes.get(k)!=old['sources'].get(k)}
        assert changed<=allowed,('Build inputs changed',changed)
        builds=[json.loads(line) for line in (previous/'build-samples.jsonl').read_text().splitlines()]
        assert len(builds)==args.build_rounds*12 and all(b['returncode']==0 for b in builds)
        shutil.copyfile(previous/'build-samples.jsonl',out/'build-samples.jsonl')
        shutil.copytree(previous/'build-logs',out/'build-logs',dirs_exist_ok=True)
        shutil.copyfile(previous/'metadata.json',out/'original-build-metadata.json')
        metadata['builds_reused_from']=str(previous);metadata['changes_since_original_build']=sorted(changed)
        (out/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    if not args.self_test:
        for round_id in range(args.build_rounds):
            for mode in ('ssr','static'):
                order=list(ENGINES);rng.shuffle(order)
                for engine in order:
                    for kind in ('cold','warm'):
                        if not args.reuse_builds:builds.append(build_trial(engine,mode,kind,round_id,out))
    deployments={engine:prepare_deployment(engine) for engine in ENGINES}
    native=Path(deployments['Axon']['entry'])
    elf=command(['readelf','-l',native]);dynamic=command(['readelf','-d',native]);symbols=command(['nm','-u',native])
    assert 'INTERP' not in elf and '(NEEDED)' not in dynamic and not symbols.strip(), 'Native runtime dependency detected'
    (out/'native-audit.json').write_text(json.dumps({'sha256':sha(native),'bytes':native.stat().st_size,'readelf_program_headers':elf,'readelf_dynamic':dynamic,'undefined_symbols':symbols,'passed':True},indent=2)+'\n')
    footprints={engine:{'app':size(deploy['directory']),'runtime_bytes':metadata['node_bytes'] if deploy['node'] else 0,'static':static_metrics(engine)} for engine,deploy in deployments.items()}
    (out/'footprints.json').write_text(json.dumps(footprints,indent=2)+'\n')
    # Actual runs cannot resolve packages or source files from original apps.
    # Every server uses its deployable output; the hidden trees are restored.
    payloads={};checks={};samples=[]
    with tempfile.TemporaryDirectory(prefix='framework-source-offline-') as temporary:
        hold=Path(temporary);moved=[]
        try:
            for name in ('apps','node_modules'):
                source=HERE/name;target=hold/name;source.rename(target);moved.append((source,target))
            for engine,deployment in deployments.items():
                with server(deployment,out/'runtime-logs'/f'verify-{engine}.log') as (process,port,startup):
                    checks[engine]={'queries':verify_server(port),'source_and_original_node_modules_offline':True,'startup_ns':startup}
                    payloads[engine]={scenario['name']:browser_payload(port,scenario) for scenario in SCENARIOS}
                    print('VERIFIED',engine,checks[engine]['queries'],'oracle cases + fresh stamps + browser asset URLs; original sources/deps offline',flush=True)
            (out/'validation.json').write_text(json.dumps(checks,indent=2)+'\n')
            (out/'payloads.json').write_text(json.dumps(payloads,indent=2)+'\n')
            if not args.self_test:
                for round_id in range(args.rounds):
                    for scenario in SCENARIOS:
                        for connections in (1,32):
                            order=list(ENGINES);rng.shuffle(order)
                            for engine in order:
                                assert shutil.disk_usage(ROOT).free>2*2**30,'Insufficient workspace free space'
                                samples.append(trial(engine,deployments[engine],scenario,connections,round_id,args,out))
        finally:
            for source,target in reversed(moved):target.rename(source)
    assert hashes=={name:sha(path) for name,path in sources().items()},'Source changed during measurements'
    summary={'complete':True,'self_test':args.self_test,'runtime_trials':len(samples),'build_trials':len(builds),'source_hashes_unchanged':True,'verification':checks,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (out/'complete.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('COMPLETE',out,flush=True)


if __name__=='__main__':main()
