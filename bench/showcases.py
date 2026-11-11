"""Measure real release showcase pages; no builds during timing."""
import argparse,hashlib,json,random,re,statistics,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bench.parity_performance import server,port,request,wrk,hardware
from bench.core_stats import Resources

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--duration',type=int,default=3);args=parser.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
    cases=[('ssr-site','/',b'Good spaces.'),('blog','/',b'Ideas worth'),('blog','/journal/a-smaller-web',b'The first response matters'),('saas','/',b'Revenue over time'),('saas','/customers?records=10000',b'10,000')]
    data={'machine':hardware(),'configuration':{'rounds':args.rounds,'duration_seconds':args.duration,'warmup_seconds':1,'connections':[1,32],'workers':1,'wrk_threads':1},'binaries':{},'samples':[]}
    for name in ('ssr-site','blog','saas'):
        binary=ROOT/'build/showcase'/name/'server';data['binaries'][name]={'path':str(binary),'bytes':binary.stat().st_size,'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
    sequence=[(round_,name,path,marker,connections) for round_ in range(args.rounds) for name,path,marker in cases for connections in (1,32)];random.Random(86429).shuffle(sequence)
    with tempfile.TemporaryDirectory(prefix='neokit-showcase-bench-') as temporary:
        directory=Path(temporary)
        for index,(round_,name,path,marker,connections) in enumerate(sequence):
            number=port();binary=ROOT/'build/showcase'/name/'server'
            with server([str(binary),str(number)],{},number,directory/'server.log') as (process,_):
                status,headers,body=request(number,path);assert status==200 and marker in body,(name,path,status)
                timing=next(value for key,value in headers.items() if key.lower()=='server-timing');assert re.fullmatch(r'render;dur=\d+\.\d{3}',timing)
                if name=='saas' and path.startswith('/customers'):assert body.count(b'data-customer-id=')==25
                script=directory/'requests.lua';script.write_text('wrk.path='+json.dumps(path)+'\nwrk.headers["Accept-Encoding"]="identity"\n')
                wrk(number,script,connections,1)
                with Resources(process) as resource:sample=wrk(number,script,connections,args.duration)
                count=int(re.search(r'(\d+) requests in',sample['raw'])[1]);sample.update(resource.metrics(count));sample.update({'round':round_,'app':name,'path':path,'connections':connections,'response_bytes':len(body),'probe_server_render_ms':float(timing.split('=')[1])})
                after=request(number,path);assert after[0]==200 and marker in after[2]
                data['samples'].append(sample);(out/'results.json').write_text(json.dumps(data,indent=2)+'\n')
                print(f'{index+1}/{len(sequence)} {name} {path} C{connections}: {sample["requests_per_sec"]:.0f} req/s, p99 {sample["p99_ms"]:.3f} ms',flush=True)
    write_report(data,out)

def write_report(data,out):
    configuration=data['configuration'];mid=lambda rows,key:statistics.median(row[key] for row in rows)
    lines=['# NeoKit showcase performance','',f"{configuration['rounds']} shuffled {configuration['duration_seconds']}-second release trials after one second warmup per case. One native worker, one wrk thread, identity encoding, no response caching. Host: {data['machine']['cpu']}. All table figures are medians across the trials, including sampled per-trial peak RSS. Actual HTML rendering for the three examples; this is not a Next/Astro feature comparison.",'',
           '| App / page | Connections | Median req/s | Client p50 ms | Client p99 ms | Sampled peak RSS MiB | CPU µs/req | CPU % of one core | HTML bytes |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    cases=list(dict.fromkeys((row['app'],row['path']) for row in data['samples']))
    for name,path in sorted(cases):
        for connections in (1,32):
            rows=[row for row in data['samples'] if row['app']==name and row['path']==path and row['connections']==connections]
            lines.append(f"| {name} `{path}` | {connections} | {mid(rows,'requests_per_sec'):,.0f} | {mid(rows,'p50_ms'):.3f} | {mid(rows,'p99_ms'):.3f} | {mid(rows,'peak_sampled_rss_kib')/1024:.1f} | {mid(rows,'cpu_us_per_request'):.2f} | {mid(rows,'cpu_percent_one_core'):.1f}% | {int(mid(rows,'response_bytes')):,} |")
    lines+=['','The SaaS directory filters/paginates 10,000 generated accounts and emits 25 rows. Charts/metrics are computed by native loaders. Blog content is compiled authored data, not database work. All pages include opt-in measured Server-Timing, whose header-encoding overhead is included in client timing. Browser badges report one buffered handler render, not total page loading or this saturated benchmark.','',
      'All trials validate status/body/timing before and after and reject wrk errors. Assets, font/image loading and browser rendering are excluded from this server matrix; actual-browser checks cover the complete pages separately. RSS is sampled every 250 ms; CPU is process-group time from ps. Other work remains active on this shared Mac, without affinity or thermal isolation. Local measurements are not production guarantees. [Raw samples and executable hashes](results.json), [actual-browser proof](browser-checks.json), [browser captures](screenshots/).','']
    worst=max(data['samples'],key=lambda row:row['p99_ms'])
    lines+=[f"The raw set retains every trial, including its highest observed p99 of {worst['p99_ms']:.2f} ms for {worst['app']} `{worst['path']}` at C{worst['connections']}. Its cause was not isolated. Median figures do not erase that observation. [Validation, bundle sizes and reproduction](VALIDATION.md) accompany the results.",'']
    (out/'REPORT.md').write_text('\n'.join(lines))
if __name__=='__main__':main()
