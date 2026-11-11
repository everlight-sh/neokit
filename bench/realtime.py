"""Release SSR before/after plus 1/100/1000 active SSE subscribers, no Redis."""
import argparse,asyncio,hashlib,json,random,re,resource,statistics,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bench.parity_performance import server,port,request,wrk,hardware
from bench.core_stats import Resources

async def subscriber(number,records,ready,index):
    reader,writer=await asyncio.open_connection('127.0.0.1',number)
    writer.write(b'GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n');await writer.drain()
    pending=b''
    try:
        header=await reader.readuntil(b'\r\n\r\n');assert header.startswith(b'HTTP/1.1 200 '),header
        assert b'text/event-stream' in header and b'Transfer-Encoding: chunked' in header
        while True:
            line=await reader.readline()
            if not line:raise EOFError('Subscriber ended during measurement')
            size=int(line.strip().split(b';')[0],16)
            if size==0:raise EOFError('Unexpected stream renewal during measurement')
            chunk=await reader.readexactly(size);assert await reader.readexactly(2)==b'\r\n'
            pending+=chunk
            while b'\n\n' in pending:
                frame,pending=pending.split(b'\n\n',1)
                fields=dict(line.split(b': ',1) for line in frame.splitlines() if b': ' in line)
                if fields.get(b'event')==b'dashboard':
                    value=json.loads(fields[b'data']);latency=time.time()*1000-value['stamp'];assert -10<=latency<10000,latency
                    records.append({'subscriber':index,'id':fields[b'id'].decode(),'delay_ms':max(0,latency),'value':value})
                    if not ready.done():ready.set_result(True)
    finally:
        writer.close();await writer.wait_closed()

async def live_trial(binary,clients,duration,folder):
    number=port();records=[];tasks=[];ready=[]
    with server([str(binary),str(number)],{},number,folder/'live.log') as (process,_):
        script=folder/'request.lua';script.write_text('wrk.path="/customers?records=10000"\n')
        for index in range(clients):
            future=asyncio.get_running_loop().create_future();ready.append(future)
            tasks.append(asyncio.create_task(subscriber(number,records,future,index)))
        try:
            await asyncio.wait_for(asyncio.gather(*ready),20)
            await asyncio.to_thread(wrk,number,script,1,1)
            begin=len(records)
            with Resources(process) as resources:sample=await asyncio.to_thread(wrk,number,script,1,duration)
            assert all(not task.done() for task in tasks),'A live subscriber dropped'
            fresh=records[begin:];assert len(fresh)>=clients,(len(fresh),clients)
            assert len({record['subscriber'] for record in fresh})==clients,'Every subscriber must receive events inside the measured window'
            count=int(re.search(r'(\d+) requests in',sample['raw'])[1]);sample.update(resources.metrics(count))
            values={}
            for record in fresh:
                value=json.dumps(record['value'],sort_keys=True)
                if record['id'] in values:assert values[record['id']]==value,'Different payloads for the same shared event'
                values[record['id']]=value
            delays=sorted(record['delay_ms'] for record in fresh)
            sample.update({'subscribers':clients,'events_received':len(fresh),'distinct_events':len(values),'event_p50_ms':statistics.median(delays),'event_p99_ms':delays[min(len(delays)-1,int(len(delays)*.99))]})
            return sample
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--duration',type=int,default=4);parser.add_argument('--live-rounds',type=int);parser.add_argument('--reuse-unaffected',type=Path);parser.add_argument('--before-dir',type=Path,default=ROOT/'build/push-before');parser.add_argument('--after-dir',type=Path,default=ROOT/'build/push');parser.add_argument('--delivery',choices=['push','polling'],default='push');parser.add_argument('--before-label',default='previous polling implementation');args=parser.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE);resource.setrlimit(resource.RLIMIT_NOFILE,(max(soft,min(4096,hard)),hard))
    data={'machine':hardware(),'configuration':{'rounds':args.rounds,'duration_seconds':args.duration,'workers':1,'wrk_threads':1,'live_ssr_connections':1,'live_subscribers':[1,100,1000]},'identities':{},'ssr':[],'live':[]}
    phases={'realtime-before':args.before_dir,'realtime':args.after_dir}
    data['configuration'].update({'before_dir':str(args.before_dir),'after_dir':str(args.after_dir),'delivery':args.delivery,'before_label':args.before_label})
    for phase in ['realtime-before','realtime']:
        for name in ['ssr-site','blog','saas']:
            binary=phases[phase]/name/'server';data['identities'][phase+'/'+name]={'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'bytes':binary.stat().st_size}
    data['configuration']['live_rounds']=args.live_rounds or args.rounds
    if args.reuse_unaffected:
        prior=json.loads(args.reuse_unaffected.read_text())
        for phase in ['realtime-before','realtime']:
            for name in ['ssr-site','blog']:assert prior['identities'][phase+'/'+name]==data['identities'][phase+'/'+name],'Reused measurements require identical executables'
        data['ssr']=[s for s in prior['ssr'] if s['app']!='saas'];data['configuration']['reused_unaffected_samples']=str(args.reuse_unaffected)
    save=lambda:(out/'results.json').write_text(json.dumps(data,indent=2)+'\n')
    cases=[('ssr-site','/'),('blog','/'),('saas','/customers?records=10000')]
    sequence=[(round_,phase,name,path) for round_ in range(args.rounds) for phase in ['realtime-before','realtime'] for name,path in cases if not args.reuse_unaffected or name=='saas']
    random.Random(93507).shuffle(sequence)
    with tempfile.TemporaryDirectory(prefix='neokit-live-bench-') as temporary:
        folder=Path(temporary)
        for round_,phase,name,path in sequence:
            number=port();binary=phases[phase]/name/'server'
            with server([str(binary),str(number)],{},number,folder/'ssr.log') as (process,_):
                status,_,body=request(number,path);assert status==200 and b'<html' in body
                if name=='saas':assert body.count(b'data-customer-id=')==25
                script=folder/'request.lua';script.write_text('wrk.path='+json.dumps(path)+'\n');wrk(number,script,32,1)
                with Resources(process) as resources:sample=wrk(number,script,32,args.duration)
                count=int(re.search(r'(\d+) requests in',sample['raw'])[1]);sample.update(resources.metrics(count));sample.update({'phase':phase,'app':name,'path':path,'round':round_,'response_bytes':len(body),'connections':32});data['ssr'].append(sample);save()
                assert request(number,path)[0]==200
                print(f'SSR {phase} {name}: {sample["requests_per_sec"]:.0f} req/s',flush=True)
        live=[(round_,clients) for round_ in range(args.live_rounds or args.rounds) for clients in [1,100,1000]];random.Random(5307).shuffle(live)
        for round_,clients in live:
            sample=asyncio.run(live_trial(args.after_dir/'saas/server',clients,args.duration,folder));sample['round']=round_;data['live'].append(sample);save()
            print(f'Live {clients}: SSR {sample["requests_per_sec"]:.0f} req/s, event p99 {sample["event_p99_ms"]:.1f} ms, RSS {sample["peak_sampled_rss_kib"]/1024:.1f} MiB',flush=True)
    report(data,out)

def report(data,out):
    median=lambda rows,key:statistics.median(row[key] for row in rows)
    lines=['# NeoKit publish-triggered SSE measurements','',f"Shuffled release trials, {data['configuration']['duration_seconds']} seconds each after warmup, one native worker and one wrk thread on {data['machine']['cpu']}. All rows are medians; trial counts are shown. No Redis or external realtime service.",'',
    '| Unchanged SSR page, 32 connections | Trials per version | Before req/s | After req/s | Change | After peak RSS MiB | After CPU µs/req |','|---|---:|---:|---:|---:|---:|---:|']
    for name in ['ssr-site','blog','saas']:
        before=[s for s in data['ssr'] if s['phase']=='realtime-before' and s['app']==name];after=[s for s in data['ssr'] if s['phase']=='realtime' and s['app']==name]
        a=median(before,'requests_per_sec');b=median(after,'requests_per_sec');lines.append(f"| {name} {'10,000-account directory' if name=='saas' else 'homepage'} | {len(after)} | {a:,.0f} | {b:,.0f} | {(b/a-1)*100:+.1f}% | {median(after,'peak_sampled_rss_kib')/1024:.1f} | {median(after,'cpu_us_per_request'):.2f} |")
    lines+=['','| Active SSE subscribers | Trials | Directory SSR req/s at C1 | SSR p99 ms | Event p50 ms | Event p99 ms | Peak RSS MiB | Total server CPU % |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for clients in [1,100,1000]:
        rows=[s for s in data['live'] if s['subscribers']==clients]
        lines.append(f"| {clients:,} | {len(rows)} | {median(rows,'requests_per_sec'):,.0f} | {median(rows,'p99_ms'):.3f} | {median(rows,'event_p50_ms'):.1f} | {median(rows,'event_p99_ms'):.1f} | {median(rows,'peak_sampled_rss_kib')/1024:.1f} | {median(rows,'cpu_percent_one_core'):.1f}% |")
    delivery='Publications wake workers through their kernel-watched IPC sockets; subscribers do not periodically poll channel data. A separate process-owned timer generates the synthetic data every two seconds.' if data['configuration'].get('delivery')=='push' else 'This historical polling version checks subscriber channels every 250 ms.'
    lines+=['','Live trials carry actual server-published demo events every two seconds while rendering the 10,000-account directory on one additional HTTP connection. Every subscriber must receive events inside the measured interval and stay connected; identical event IDs must carry identical payloads. Delivery delay uses the publisher wall-clock timestamp and receipt on the same Mac, including load-generator scheduling. '+delivery+' Clients resume a bounded history; these are synthetic events, not external financial transactions.','',
    'The first table compares unchanged page workloads against preserved binaries from the '+data['configuration'].get('before_label','previous polling implementation')+'. The SaaS comparison uses the same 10,000-account directory, with 25 rendered rows. CPU/RSS are server-process-group samples; live CPU includes both SSR and event delivery, so it is not attributed per HTML request. One worker supports 1,024 HTTP connections, including subscribers. The load generator is excluded from server CPU/RSS.','',
    'This is a shared Mac with no thermal/CPU isolation. Short trials and 250 ms RSS sampling have limited precision. Raw samples, outliers and binary identities are retained in [results.json](results.json). [Protocol verification](protocol.json), [browser checks](browser-checks.json), [validation and API scope](VALIDATION.md). Browser checks run separately from performance trials.','']
    if data['configuration'].get('reused_unaffected_samples'):lines+=['The initial full run and its 6% customer-page median drop are retained in [initial/results.json](initial/results.json). A framework dispatch change now tests a stream route match before walking its higher-priority exclusions. Customer and live cases were rerun; the unaffected site/blog samples were retained only after checking that both executable identities are unchanged. This is a local follow-up, not a claim that dispatch accounts for every initial timing difference.','']
    large=[row['event_p99_ms'] for row in data['live'] if row['subscribers']==1000]
    lines+=[f'At 1,000 subscribers, individual trial event p99 values range from {min(large):.1f} to {max(large):.1f} ms. All trials are retained; these local delivery times are not latency guarantees.','']
    protocol=out/'protocol.json'
    if protocol.exists():
        verified=json.loads(protocol.read_text());latency=verified.get('arbitrary_publish_delivery_ms')
        if latency:lines += [f"An independent protocol test publishes at irregular intervals, separate from the demo timer: macOS POST-to-both-subscribers delay was {latency['p50']:.2f} ms median, {latency['p95']:.2f} ms p95 and {latency['max']:.2f} ms maximum. Idle subscription checks recorded {verified['idle_channel_reads']} channel reads and {verified['idle_subscriber_callbacks']} callbacks. A separate heartbeat assertion verifies it does not read the channel. See [validation](VALIDATION.md) for Linux results, replay timing and regression checks.",'']
    (out/'REPORT.md').write_text('\n'.join(lines))
if __name__=='__main__':main()
