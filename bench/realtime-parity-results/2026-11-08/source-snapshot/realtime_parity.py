"""Matched production SSE fanout: NeoKit / Next / Astro, one server worker.

Python is an independent publish/subscribe peer. Fixed-rate samples require every
subscriber to receive every exact payload, in order, without reset/disconnection.
No SSR, browser rendering, database, external service or Redis is measured.
"""
import argparse,asyncio,hashlib,json,os,random,resource,shutil,statistics,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bench.core_stats import Resources
from bench.parity_performance import server,port,hardware

def prepare():
    suite=ROOT/'build/realtime-parity';suite.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'bench/realtime_apps/hub.mjs',suite/'hub.mjs')
    for name in ('next','astro'):
        app=suite/name;app.mkdir(exist_ok=True)
        modules=ROOT.parent/'makecademy/node_modules' if name=='next' else ROOT/'build/performance/astro-suite/node_modules'
        if not (app/'node_modules').exists():(app/'node_modules').symlink_to(modules,target_is_directory=True)
        package={'name':'sse-parity-'+name,'private':True,'type':'module','scripts':{'build':'next build --webpack' if name=='next' else 'astro build'}}
        (app/'package.json').write_text(json.dumps(package))
        if name=='next':
            (app/'next.config.mjs').write_text('export default {experimental:{cpus:1},poweredByHeader:false,outputFileTracingRoot:'+json.dumps(str(suite))+'};\n')
            (app/'app').mkdir(exist_ok=True)
            (app/'app/layout.jsx').write_text('export default function Layout({children}){return <html><body>{children}</body></html>}\n')
            (app/'app/page.jsx').write_text('export default function Page(){return <p>Realtime parity</p>}\n')
            for path,handler,method in [('feed','feed','GET'),('publish','publish','POST'),('stats','stats','GET')]:
                folder=app/'app'/path;folder.mkdir(exist_ok=True)
                (folder/'route.js').write_text(f"import {{{handler}}} from '../../../hub.mjs';\nexport const runtime='nodejs';\nexport const dynamic='force-dynamic';\nexport const {method}={handler};\n")
        else:
            (app/'astro.config.mjs').write_text("import {defineConfig} from 'astro/config';import node from '@astrojs/node';export default defineConfig({output:'server',adapter:node({mode:'standalone'}),session:false});\n")
            folder=app/'src/pages';folder.mkdir(parents=True,exist_ok=True)
            for path,handler,method in [('feed','feed','GET'),('publish','publish','POST'),('stats','stats','GET')]:
                (folder/(path+'.js')).write_text(f"import {{{handler}}} from '../../../hub.mjs';\nexport const prerender=false;\nexport const {method}=({{request}})=>{handler}(request);\n")
        with (suite/(name+'-build.log')).open('w') as log:
            subprocess.run(['bun','run','build'],cwd=app,env={**os.environ,'NODE_ENV':'production','NEXT_TELEMETRY_DISABLED':'1','ASTRO_TELEMETRY_DISABLED':'1'},stdout=log,stderr=log,check=True)
        print('Built production '+name,flush=True)

class HTTP:
    def __init__(self,number):self.number=number;self.reader=None;self.writer=None
    async def request(self,path,method='GET',body=b'',headers=None):
        if self.writer is None:self.reader,self.writer=await asyncio.open_connection('127.0.0.1',self.number)
        values={'Host':'localhost','Content-Length':str(len(body)),'Content-Type':'application/json','Connection':'keep-alive',**(headers or {})}
        wire=f'{method} {path} HTTP/1.1\r\n'+''.join(f'{key}: {value}\r\n' for key,value in values.items())+'\r\n'
        self.writer.write(wire.encode()+body);await self.writer.drain()
        head=await self.reader.readuntil(b'\r\n\r\n');lines=head.decode().split('\r\n');status=int(lines[0].split()[1]);fields={key.lower():value.strip() for line in lines[1:] if ':' in line for key,value in [line.split(':',1)]}
        if fields.get('transfer-encoding')=='chunked':
            parts=[]
            while True:
                size=int((await self.reader.readline()).strip().split(b';')[0],16)
                if not size:assert await self.reader.readline()==b'\r\n';break
                parts.append(await self.reader.readexactly(size));assert await self.reader.readexactly(2)==b'\r\n'
            result=b''.join(parts)
        else:result=await self.reader.readexactly(int(fields.get('content-length','0')))
        if fields.get('connection','').lower()=='close':await self.close()
        return status,result
    async def close(self):
        if self.writer:
            self.writer.close();await self.writer.wait_closed();self.writer=None

async def stream(number,index,state,ready,cursor=None):
    reader,writer=await asyncio.open_connection('127.0.0.1',number)
    header=f'Last-Event-ID: {cursor}\r\n' if cursor else ''
    writer.write(('GET /feed HTTP/1.1\r\nHost: localhost\r\nCookie: session=allowed\r\n'+header+'\r\n').encode());await writer.drain()
    try:
        head=await reader.readuntil(b'\r\n\r\n');assert head.startswith(b'HTTP/1.1 200 '),head
        assert b'text/event-stream' in head.lower() and b'transfer-encoding: chunked' in head.lower(),head
        pending=b''
        while True:
            line=await reader.readline()
            if not line:raise EOFError('SSE disconnected')
            size=int(line.strip().split(b';')[0],16)
            if not size:raise EOFError('SSE ended')
            pending+=await reader.readexactly(size);assert await reader.readexactly(2)==b'\r\n'
            while b'\n\n' in pending:
                frame,pending=pending.split(b'\n\n',1);fields=dict(line.split(b': ',1) for line in frame.splitlines() if b': ' in line)
                kind=fields.get(b'event')
                if kind==b'neokit-ready':
                    if not ready.done():ready.set_result(True)
                elif kind==b'neokit-reset':raise AssertionError('Unexpected replay reset')
                elif kind==b'message':
                    received=time.perf_counter_ns();value=json.loads(fields[b'data']);counter=value['counter']
                    # Exact content, order, no duplicate or skipped event for each subscriber.
                    assert value==state['expected'][counter],value
                    last=state['last'].get(index,counter-1);assert counter==last+1,(index,last,counter)
                    state['last'][index]=counter;state['cursors'][counter]=fields[b'id'].decode()
                    state['latencies'].append((received-state['sent'][counter])/1e6)
                    count=state['counts'].get(counter,0)+1;state['counts'][counter]=count
                    if count==state['clients']:
                        state['cohort'][counter]=(received-state['sent'][counter])/1e6
                        state['done'][counter].set_result(True)
    finally:
        writer.close();await writer.wait_closed()

def percentile(values,fraction):
    ordered=sorted(values);return ordered[min(len(ordered)-1,int(len(ordered)*fraction))]

async def exercise(number,clients,process,args):
    state={'clients':clients,'expected':{},'sent':{},'done':{},'counts':{},'latencies':[],'cohort':{},'last':{},'cursors':{}}
    loop=asyncio.get_running_loop();http=HTTP(number);tasks=[];ready=[];counter=0
    async def publish():
        nonlocal counter
        counter+=1;value={'counter':counter,'text':'<café> & live','padding':'x'*128}
        body=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode();state['expected'][counter]=value;state['done'][counter]=loop.create_future();state['sent'][counter]=time.perf_counter_ns()
        status,result=await asyncio.wait_for(http.request('/publish','POST',body),5);assert status==200,(status,result)
        return counter
    async def settled(sequence):
        await asyncio.wait_for(asyncio.shield(state['done'][sequence]),5)
        for task in tasks:
            if task.done():
                task.result()
                raise AssertionError('Subscriber ended')
    try:
        # Explicitly check access control and invalid data on each fresh server.
        assert (await http.request('/feed'))[0]==403
        assert (await http.request('/publish','POST',b'not-json'))[0]==400
        assert (await http.request('/publish','POST',json.dumps({'text':'x'*5000}).encode()))[0]==400
        for index in range(clients):
            future=loop.create_future();ready.append(future);tasks.append(asyncio.create_task(stream(number,index,state,future)))
            # Connection admission is untimed. Avoid overflowing the OS accept
            # backlog with 1,000 simultaneous handshakes on the shared Mac.
            if len(ready)%32==0:await asyncio.wait_for(asyncio.gather(*ready[-32:]),15)
        try:await asyncio.wait_for(asyncio.gather(*ready),30)
        except BaseException:
            for task in tasks:
                if task.done() and not task.cancelled():task.result()
            raise
        for _ in range(5):await settled(await publish())
        with Resources(process) as idle:
            await asyncio.sleep(2)
        idle_metrics=idle.metrics(1)
        start_counter=counter+1;state['latencies'].clear();began=time.perf_counter()
        with Resources(process) as active:
            for index in range(args.rate*args.duration):
                delay=began+index/args.rate-time.perf_counter()
                if delay>0:await asyncio.sleep(delay)
                await publish()
            await settled(counter)
            # Keep the last period inside the measurement for equal offered load.
            await asyncio.sleep(max(0,began+args.duration-time.perf_counter()))
        count=args.rate*args.duration;metrics=active.metrics(count)
        assert sum(state['counts'][i] for i in range(start_counter,counter+1))==clients*count
        fixed={'publishes':count,'deliveries':clients*count,'publishes_per_sec':count/active.metrics(count)['resource_wall_seconds'],'deliveries_per_sec':clients*count/active.metrics(count)['resource_wall_seconds'],
               'delivery_p50_ms':statistics.median(state['latencies']),'delivery_p99_ms':percentile(state['latencies'],.99),
               'all_subscribers_p50_ms':statistics.median(state['cohort'][i] for i in range(start_counter,counter+1)),
               'all_subscribers_p99_ms':percentile([state['cohort'][i] for i in range(start_counter,counter+1)],.99),**metrics}
        # A separate closed-loop ceiling includes the same peer's scheduling.
        burst_start=time.perf_counter();burst_count=0
        while time.perf_counter()-burst_start<args.burst_seconds:
            await settled(await publish());burst_count+=1
        burst_elapsed=time.perf_counter()-burst_start
        # Check cursor replay after closing all original subscribers.
        saved=state['cursors'][counter]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True);tasks.clear()
        a=await publish();b=await publish()
        # Replayed events must reach the new single subscriber in order.
        state['clients']=1;state['last'].clear();future=loop.create_future();tasks.append(asyncio.create_task(stream(number,0,state,future,saved)))
        await asyncio.wait_for(future,5);await settled(a);await settled(b)
        return {'subscribers':clients,'idle':idle_metrics,'fixed_rate':fixed,'closed_loop':{'publishes':burst_count,'elapsed_seconds':burst_elapsed,'publish_and_deliver_to_all_per_sec':burst_count/burst_elapsed},'replay':'PASS exact retained events'}
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True);await http.close()
        # Both adapters must release their process-owned subscriptions on EOF.
        check=HTTP(number)
        try:
            for _ in range(100):
                status,body=await check.request('/stats');value=json.loads(body)
                if value['subscribers']==0:break
                await asyncio.sleep(.02)
            else:raise AssertionError(('Subscription cleanup failed',value))
        finally:await check.close()

def specification(name,number):
    suite=ROOT/'build/realtime-parity'
    if name=='neokit':return [str(ROOT/'build/push/fixture/server'),str(number)],{}
    if name=='next':return ['node',str(suite/'next/node_modules/next/dist/bin/next'),'start',str(suite/'next'),'-p',str(number),'-H','127.0.0.1'],{}
    return ['node',str(suite/'astro/dist/server/entry.mjs')],{'HOST':'127.0.0.1','PORT':str(number),'ASTRO_TELEMETRY_DISABLED':'1'}

def report(data,out):
    median=lambda rows,path:statistics.median(__import__('functools').reduce(lambda value,key:value[key],path.split('.'),row) for row in rows)
    summary=[];lines=['# Production SSE: NeoKit vs Next vs Astro','',
      f"Installed versions: Next {data['versions']['next']}; Astro {data['versions']['astro']} with node adapter {data['versions']['astro_node']}; Node {data['versions']['node']}. NeoKit prepared release executable. Host: {data['machine']['cpu']}. One server worker/process, no Redis.",'',
      f"{data['configuration']['rounds']} shuffled rounds per case. {data['configuration']['rate']} external JSON publications/second for {data['configuration']['duration']} seconds after warmup, each sent to every SSE subscriber. Payloads, order and counts are verified, as are access control, invalid input, replay and disconnect cleanup. Every trial must have zero dropped, duplicate or unexpected events.",'',
      '| Subscribers | Framework | Delivery p50 ms | Delivery p99 ms | Last-subscriber p99 ms | Server RSS MiB | Active CPU % one core | Idle CPU % one core | Serial publish + all-delivered /s |','|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for clients in (1,100,1000):
        for framework in ('neokit','next','astro'):
            rows=[r for r in data['samples'] if r['framework']==framework and r['subscribers']==clients]
            if not rows:continue
            row={'framework':framework,'subscribers':clients,'trials':len(rows),'p50_ms':median(rows,'fixed_rate.delivery_p50_ms'),'p99_ms':median(rows,'fixed_rate.delivery_p99_ms'),'all_p99_ms':median(rows,'fixed_rate.all_subscribers_p99_ms'),'rss_mib':median(rows,'fixed_rate.peak_sampled_rss_kib')/1024,'cpu_percent':median(rows,'fixed_rate.cpu_percent_one_core'),'idle_cpu_percent':median(rows,'idle.cpu_percent_one_core'),'serial_publishes_per_sec':median(rows,'closed_loop.publish_and_deliver_to_all_per_sec')};summary.append(row)
            lines.append(f"| {clients:,} | {framework} | {row['p50_ms']:.3f} | {row['p99_ms']:.3f} | {row['all_p99_ms']:.3f} | {row['rss_mib']:.1f} | {row['cpu_percent']:.2f} | {row['idle_cpu_percent']:.2f} | {row['serial_publishes_per_sec']:,.1f} |")
    lines+=['','## Method and limits','',
      'All three use publish-triggered SSE over local HTTP/1.1 with 128 retained events, a 4,096-byte JSON payload bound, a fixed cookie check and explicit subscriber cleanup. The Next/Astro application shares the same bounded in-process event hub and WHATWG ReadableStream implementation; their route/adapter overhead remains included. NeoKit uses its native shared-memory framework channel, with extra untimed fixture routes present. These fixtures measure local single-server realtime delivery, not complete application feature parity.','',
      'Delay starts on the independent client before the publish POST and ends when that client parses the received event. Delivery p50/p99 pool every subscriber/event observation. Last-subscriber p99 measures completion of the full fanout per publication. The client checks exact JSON content and consecutive event counters; a reset, missing event or closed stream fails the run. The HTTP publish response may arrive after event delivery.','',
      'Fixed-rate event throughput is an offered workload, not a saturation claim: 10 publications/second at 1,000 subscribers equals 10,000 event deliveries/second. The separate serial rate is a closed-loop publish-and-wait-for-every-subscriber loop, not maximum open-loop server capacity. Its Python parsing/fanout and loopback costs can dominate, especially at 1,000 subscribers. No application SSR runs concurrently in this matrix.','',
      'CPU is process-group CPU-time delta from ps (10 ms resolution); idle CPU over two seconds has limited precision. RSS is a 250 ms sample, after connections/warmup, excluding the load generator. One server process can use background threads; Node CPU may exceed 100%. All apps run in production mode with no deliberate GC. Framework memory includes warmed production routing/runtime costs.','',
      'Client and server share an unisolated Mac with uncontrolled thermals and background load. These short local trials measure neither internet/network distance, TLS/proxies, browser rendering, websockets, durable queues, multiple machines nor hosted/serverless limits. A smaller payload or different event rate/application can change rankings. Package versions identify locally installed builds, not the latest releases. [Raw samples and identities](results.json), [summary](summary.json), [fixture source](../../realtime_apps/hub.mjs).','',
      'Framework APIs: [Next Route Handlers](https://nextjs.org/docs/app/api-reference/file-conventions/route), [Astro endpoints](https://docs.astro.build/en/guides/endpoints/). The shared hub is a benchmark application, not a claim that either framework has NeoKit-compatible channels built in.','']
    (out/'REPORT.md').write_text('\n'.join(lines));(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare',action='store_true');parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--duration',type=int,default=4);parser.add_argument('--rate',type=int,default=10);parser.add_argument('--burst-seconds',type=float,default=1);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--clients',type=int,nargs='+',default=[1,100,1000]);parser.add_argument('--frameworks',choices=['neokit','next','astro'],nargs='+',default=['neokit','next','astro']);args=parser.parse_args()
    if args.prepare:prepare()
    args.out.mkdir(parents=True,exist_ok=True)
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE);resource.setrlimit(resource.RLIMIT_NOFILE,(max(soft,min(4096,hard)),hard))
    suite=ROOT/'build/realtime-parity';version=lambda path:json.loads(path.read_text())['version']
    data={'machine':hardware(),'configuration':{k:v for k,v in vars(args).items() if k not in ('out','prepare')},'versions':{'next':version(suite/'next/node_modules/next/package.json'),'astro':version(suite/'astro/node_modules/astro/package.json'),'astro_node':version(suite/'astro/node_modules/@astrojs/node/package.json'),'node':subprocess.check_output(['node','--version'],text=True).strip()},'identities':{},'samples':[]}
    for name,path in [('neokit',ROOT/'build/push/fixture/server'),('hub',suite/'hub.mjs'),('harness',Path(__file__)),('next-build',suite/'next/.next/BUILD_ID'),('astro-entry',suite/'astro/dist/server/entry.mjs')]:data['identities'][name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
    for name,folder in [('next-server',suite/'next/.next/server'),('astro-dist',suite/'astro/dist')]:
        digest=hashlib.sha256()
        for path in sorted(p for p in folder.rglob('*') if p.is_file()):digest.update(str(path.relative_to(folder)).encode());digest.update(path.read_bytes())
        data['identities'][name]={'sha256':digest.hexdigest()}
    sequence=[(round_,framework,clients) for round_ in range(args.rounds) for framework in args.frameworks for clients in args.clients];random.Random(44187).shuffle(sequence)
    for round_,framework,clients in sequence:
        number=port();command,environment=specification(framework,number);log=args.out/f'{round_}-{framework}-{clients}.log'
        print(f'Start {framework} {clients} round {round_}',flush=True)
        with server(command,environment,number,log) as (process,startup):
            sample=asyncio.run(exercise(number,clients,process,args));sample.update({'round':round_,'framework':framework,'startup_socket_ms':startup});data['samples'].append(sample)
        (args.out/'results.json').write_text(json.dumps(data,indent=2)+'\n')
        print(f"{framework} {clients}: p50 {sample['fixed_rate']['delivery_p50_ms']:.3f} ms / p99 {sample['fixed_rate']['delivery_p99_ms']:.3f} ms, RSS {sample['fixed_rate']['peak_sampled_rss_kib']/1024:.1f} MiB",flush=True)
    report(data,args.out)
if __name__=='__main__':main()
