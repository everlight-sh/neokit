"""Matched production SSR: NeoKit / Next / Astro, with sampled process resources.

One server worker/process and one wrk thread. No installs or builds are performed.
The primary NeoKit binary uses prepared libraries. All versions are recorded.
"""
import argparse,concurrent.futures,csv,hashlib,http.client,json,os,random,re,statistics,subprocess,sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bench.parity_performance import ROOT,server,port,hardware,wrk,request
from bench.frameworks.model import verify_server,SCENARIOS,validate,get

def cpu_seconds(value):
    days=0
    if '-' in value:day,value=value.split('-',1);days=int(day)
    parts=list(map(float,value.split(':')))
    return days*86400+sum(number*60**index for index,number in enumerate(reversed(parts)))

def snapshot(group):
    rows=subprocess.check_output(['ps','-axo','pid=,pgid=,rss=,time='],text=True).splitlines()
    result={}
    for row in rows:
        fields=row.split()
        if len(fields)==4 and int(fields[1])==group:result[int(fields[0])]={'rss_kib':int(fields[2]),'cpu_seconds':cpu_seconds(fields[3])}
    return result

class Resources:
    def __init__(self,process):self.process=process;self.stop=threading.Event();self.samples=[]
    def __enter__(self):
        self.before=snapshot(self.process.pid);self.start=time.perf_counter();self.samples.append(self.before)
        def sample():
            while not self.stop.wait(.25):self.samples.append(snapshot(self.process.pid))
        self.thread=threading.Thread(target=sample,daemon=True);self.thread.start();return self
    def __exit__(self,*_):
        self.end=time.perf_counter();self.after=snapshot(self.process.pid);self.samples.append(self.after)
        self.stop.set();self.thread.join()
    def metrics(self,requests):
        elapsed=self.end-self.start
        cpu=sum(max(0,info['cpu_seconds']-self.before.get(pid,{'cpu_seconds':0})['cpu_seconds']) for pid,info in self.after.items())
        return {'resource_wall_seconds':elapsed,'server_cpu_seconds':cpu,'cpu_percent_one_core':cpu/elapsed*100,
                'cpu_us_per_request':cpu*1e6/requests,'peak_sampled_rss_kib':max(sum(p['rss_kib'] for p in sample.values()) for sample in self.samples),
                'start_rss_kib':sum(p['rss_kib'] for p in self.before.values()),'resource_samples':len(self.samples)}

def specification(name,number,neokit_binary=None):
    modules=ROOT.parent/'makecademy/node_modules'
    if name=='neokit':return [str(neokit_binary or ROOT/'build/cache-async/catalog/server'),str(number)],{}
    if name=='next':return ['node',str(modules/'next/dist/bin/next'),'start',str(ROOT/'build/performance/next-suite/apps/next'),'-p',str(number),'-H','127.0.0.1'],{}
    return ['node',str(ROOT/'build/performance/astro-suite/apps/astro/dist-ssr/server/entry.mjs')],{'HOST':'127.0.0.1','PORT':str(number),'BENCH_MODE':'ssr','ASTRO_TELEMETRY_DISABLED':'1'}

def fixed_rate(number,rate,duration):
    local=threading.local();connections=[];lock=threading.Lock();latencies=[]
    def one(index):
        if not hasattr(local,'connection'):
            local.connection=http.client.HTTPConnection('127.0.0.1',number,timeout=10)
            with lock:connections.append(local.connection)
        start=time.perf_counter();token='fixed-'+str(index)
        local.connection.request('GET','/?n=3&seed=17&token='+token,headers={'Accept-Encoding':'identity'})
        response=local.connection.getresponse();body=response.read()
        assert response.status==200 and token.encode() in body,(response.status,body[:100])
        return (time.perf_counter()-start)*1000
    began=time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures=[]
        for index in range(rate*duration):
            delay=began+index/rate-time.perf_counter()
            if delay>0:time.sleep(delay)
            futures.append(pool.submit(one,index))
        for future in futures:latencies.append(future.result())
    elapsed=time.perf_counter()-began
    for connection in connections:connection.close()
    ordered=sorted(latencies)
    return {'requests':len(latencies),'requests_per_sec':len(latencies)/elapsed,'p50_ms':ordered[len(ordered)//2],
            'p99_ms':ordered[min(len(ordered)-1,int(len(ordered)*.99))],'target_rate':rate,'elapsed_seconds':elapsed}

def report(data,out):
    browser_evidence='browser-checks.json' if (out/'browser-checks.json').exists() else '../../async-results/2026-11-08/browser-final.json'
    median=lambda rows,field:statistics.median(row[field] for row in rows)
    selected=lambda framework,case='small-ssr',connections=32:[s for s in data['samples'] if s['framework']==framework and s['case']==case and s['connections']==connections]
    lines=['# NeoKit, Next and Astro: core server measurements','',
      'Production uncached SSR with matched table data, escaping, query variation, freshness and a small counter. One server worker/process, one wrk thread, identity encoding. NeoKit uses the normal prepared-library build, with current HTTP runtime options enabled.','',
      f"Versions: NeoKit {data['versions']['neokit']}; Next {data['versions']['next']}; Astro {data['versions']['astro']} / node adapter {data['versions']['astro_node']}; Node {data['versions']['node']}. Host: {data['machine']['cpu']}, {data['machine']['logical_cpus']} logical CPUs, {data['machine']['memory_bytes']/2**30:.1f} GiB RAM.",'',
      f"{data['configuration']['rounds']} shuffled rounds, {data['configuration']['duration_s']} seconds measured after one second of warmup per case; 10-second client timeout. Fixed-rate probes separately target {data['configuration']['fixed_rps']} small pages/sec for {data['configuration']['fixed_seconds']} seconds.",'',
      '| Framework | Small SSR req/s, C32 | p50 ms, C1 | p99 ms, C32 | Peak sampled RSS MiB, C32 | CPU µs/request, C32 | CPU % of one core at fixed rate |',
      '|---|---:|---:|---:|---:|---:|---:|']
    summary=[]
    for name,label in [('neokit','NeoKit (prepared)'),('next','Next'),('astro','Astro')]:
        saturated=selected(name);single=selected(name,connections=1);fixed=[s for s in data['fixed_rate'] if s['framework']==name]
        row={'framework':name,'requests_per_sec':median(saturated,'requests_per_sec'),'p50_single_ms':median(single,'p50_ms'),
             'p99_saturated_ms':median(saturated,'p99_ms'),'peak_rss_mib':median(saturated,'peak_sampled_rss_kib')/1024,
             'cpu_us_per_request':median(saturated,'cpu_us_per_request'),'fixed_cpu_percent_one_core':median(fixed,'cpu_percent_one_core')}
        summary.append(row)
        lines.append(f"| {label} | {row['requests_per_sec']:,.1f} | {row['p50_single_ms']:.3f} | {row['p99_saturated_ms']:.3f} | {row['peak_rss_mib']:.1f} | {row['cpu_us_per_request']:.2f} | {row['fixed_cpu_percent_one_core']:.2f} |")
    lines+=['','CPU at saturation is normalized per request: similar total CPU can deliver very different throughput. Fixed-rate CPU is a separate equal-load comparison, not CPU at peak throughput. RSS is the median of each trial’s peak sampled process-group RSS, not a guaranteed instantaneous high-water mark.','',
       '| Workload | Connections | Framework | Median req/s | p50 ms | p99 ms | Sampled RSS MiB | CPU % one core | CPU µs/request |','|---|---:|---|---:|---:|---:|---:|---:|---:|']
    for case in [s['name'] for s in SCENARIOS]:
        for count in (1,32):
            for name in ('neokit','next','astro'):
                rows=selected(name,case,count)
                lines.append(f"| {case} | {count} | {name} | {median(rows,'requests_per_sec'):,.1f} | {median(rows,'p50_ms'):.3f} | {median(rows,'p99_ms'):.3f} | {median(rows,'peak_sampled_rss_kib')/1024:.1f} | {median(rows,'cpu_percent_one_core'):.2f} | {median(rows,'cpu_us_per_request'):.2f} |")
    lines+=['','| Framework | Socket readiness ms | First HTTP readiness ms | Small HTML bytes | Large HTML bytes | Interactive HTML bytes |','|---|---:|---:|---:|---:|---:|']
    for name in ('neokit','next','astro'):
        rows=[s for s in data['samples'] if s['framework']==name]
        size=lambda case:int(median(selected(name,case),'response_bytes'))
        lines.append(f"| {name} | {median(rows,'startup_socket_ms'):.2f} | {median(rows,'startup_http_ms'):.2f} | {size('small-ssr'):,} | {size('large-ssr'):,} | {size('interactive-ssr'):,} |")
    lines+=['','## Scope and limits','',
      'Every trial validates rendered cells, checksum, UTF-8/escaping, changed query tokens and fresh timestamps before and after timing. Socket errors and non-success responses fail the run. Workloads use 3, 1000 and 50 rows. Browser asset loading, hydration, database writes, auth, distributed caches, build speed, Linux performance and full feature parity are not measured by this matrix.','',
      'HTML wire sizes differ: Next includes App Router/RSC serialized data and bootstrap, while Astro and NeoKit use HTML with small widget modules. Those architectural costs are included. CPU comes from process-group CPU-time deltas using ps; 10 ms clock resolution limits low-CPU accuracy. Sampling RSS at 250 ms can miss brief peaks. Already-exited child CPU is not included. CPU can exceed 100% because Node/V8 background threads can use multiple cores despite one server process. Pre-trial semantic validation touches all three routes, so RSS includes a warmed fixture rather than an isolated cold small-page route. Startup means fresh process readiness with warm OS caches, not a cold machine.','',
      f'All servers and the client share this busy Mac, with no affinity control, controlled thermals or formal confidence intervals. Tests are interleaved to reduce ordering bias, not eliminate it. Do not extrapolate these local fixtures into a universal framework ranking. Version numbers identify the actual installed packages; this is not an assertion that all are the latest releases. [Raw samples and binary/source identities](results.json), [summary JSON](summary.json), [throughput/resource CSV](samples.csv), [fixed-rate CSV](fixed-rate.csv), and [separate async/browser checks]({browser_evidence}) accompany the report. [The prepared-library authentication supplement](PREPARED.md) separately checks the compiler fix against a saved prepared binary and the current monolithic build.','']
    (out/'REPORT.md').write_text('\n'.join(lines));(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    for filename,rows in [('samples.csv',data['samples']),('fixed-rate.csv',data['fixed_rate'])]:
        fields=sorted(set().union(*(row.keys() for row in rows))-{'raw'})
        with (out/filename).open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=ROOT/'bench/core-results/2026-11-07')
    parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--duration',type=int,default=4);parser.add_argument('--fixed-rps',type=int,default=200);parser.add_argument('--fixed-seconds',type=int,default=6)
    parser.add_argument('--neokit-binary',type=Path,default=ROOT/'build/cache-async/catalog/server')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    package=lambda name:json.loads(Path(name).read_text())['version']
    data={'configuration':{'rounds':args.rounds,'duration_s':args.duration,'warmup_s':1,'connections':[1,32],'fixed_rps':args.fixed_rps,'fixed_seconds':args.fixed_seconds},
          'machine':hardware(),'environment':{'load_average':list(os.getloadavg())},'versions':{'neokit':'0.1.0-dev.1','next':package(ROOT.parent/'makecademy/node_modules/next/package.json'),
            'astro':package(ROOT/'build/performance/astro-suite/node_modules/astro/package.json'),'astro_node':package(ROOT/'build/performance/astro-suite/node_modules/@astrojs/node/package.json'),'node':subprocess.check_output(['node','--version'],text=True).strip()},
          'identities':{},'samples':[],'fixed_rate':[]}
    for name,path in [('neokit',args.neokit_binary.resolve()),('next-build',ROOT/'build/performance/next-suite/apps/next/.next/BUILD_ID'),('astro-entry',ROOT/'build/performance/astro-suite/apps/astro/dist-ssr/server/entry.mjs')]:
        data['identities'][name]={'path':str(path),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    for name,path in [('astro-dist',ROOT/'build/performance/astro-suite/apps/astro/dist-ssr'),('next-server',ROOT/'build/performance/next-suite/apps/next/.next/server')]:
        digest=hashlib.sha256()
        for file in sorted(p for p in path.rglob('*') if p.is_file()):digest.update(str(file.relative_to(path)).encode());digest.update(file.read_bytes())
        data['identities'][name]={'sha256':digest.hexdigest()}
    sequence=[(round_,case,connections,name) for round_ in range(args.rounds) for case in SCENARIOS for connections in (1,32) for name in ('neokit','next','astro')]
    random.Random(90722).shuffle(sequence)
    def save(): (args.out/'results.json').write_text(json.dumps(data,indent=2)+'\n')
    with tempfile.TemporaryDirectory(prefix='neokit-core-stats-') as temporary:
        directory=Path(temporary)
        for index,(round_,case,connections,name) in enumerate(sequence):
            number=port();command,environment=specification(name,number,args.neokit_binary.resolve())
            with server(command,environment,number,directory/'server.log') as (process,startup):
                _,_,body,_,first_http=get(number,'/?n=3&seed=17&token=first');verify_server(number)
                script=directory/'requests.lua';path=case['path']+f'?n={case["n"]}&seed=17&token='
                script.write_text('counter=0\nrequest=function() counter=counter+1; return wrk.format("GET",'+json.dumps(path)+'..counter,{["Accept-Encoding"]="identity"}) end\n')
                payload=request(number,path+'validation')[2];validate(payload,case['n'],17,'validation',case['interactive'])
                wrk(number,script,connections,1)
                with Resources(process) as resource:sample=wrk(number,script,connections,args.duration)
                count=int(re.search(r'(\d+) requests in',sample['raw'])[1]);sample.update(resource.metrics(count))
                sample.update({'round':round_,'framework':name,'case':case['name'],'connections':connections,'requests':count,'response_bytes':len(payload),'startup_socket_ms':startup,'startup_http_ms':startup+first_http/1e6})
                verify_server(number);data['samples'].append(sample);save()
                print(f'{index+1}/{len(sequence)} {name} {case["name"]} C{connections}: {sample["requests_per_sec"]:.1f} req/s, {sample["cpu_us_per_request"]:.2f} CPU us/req, {sample["peak_sampled_rss_kib"]/1024:.1f} MiB',flush=True)
        fixed=[(round_,name) for round_ in range(args.rounds) for name in ('neokit','next','astro')];random.Random(7713).shuffle(fixed)
        for index,(round_,name) in enumerate(fixed):
            number=port();command,environment=specification(name,number,args.neokit_binary.resolve())
            with server(command,environment,number,directory/'server.log') as (process,_):
                verify_server(number);script=directory/'fixed.lua';script.write_text('wrk.path="/?n=3&seed=17&token=warmup"\nwrk.headers["Accept-Encoding"]="identity"\n');wrk(number,script,1,1)
                with Resources(process) as resource:sample=fixed_rate(number,args.fixed_rps,args.fixed_seconds)
                sample.update(resource.metrics(sample['requests']));sample.update({'framework':name,'round':round_});data['fixed_rate'].append(sample);save()
                verify_server(number);print(f'Fixed {index+1}/{len(fixed)} {name}: {sample["requests_per_sec"]:.1f} req/s, {sample["cpu_percent_one_core"]:.2f}% CPU',flush=True)
    report(data,args.out)

if __name__=='__main__':main()
