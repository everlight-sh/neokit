"""Measure fast-route throughput while real network/database loaders are waiting.

Owns delayed HTTP peers and NeoKit processes; --pg-port identifies the independent
disposable SCRAM PostgreSQL test service. No builds or installs during timing.
"""
import argparse,concurrent.futures,hashlib,json,os,random,re,statistics,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from async_rendering import PeerServer,Peer,request,quiet
from bench.core_stats import Resources
from bench.parity_performance import hardware,port,server,wrk

CASES = (
    ('idle', 0, ''),
    ('network-waits', 32, '/async-api?target=/hold'),
    ('database-and-network-streams', 16, '/async'),
)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--pg-port',type=int,required=True)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--duration',type=int,default=4)
    parser.add_argument('--case',choices=[name for name,_,_ in CASES],help='Run only this load case')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True);binary=args.binary.resolve()
    cases=[case for case in CASES if args.case is None or case[0]==args.case]
    data={'machine':hardware(),'load_average':list(os.getloadavg()),'binary':{'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()},
          'configuration':{'workers':1,'wrk_threads':1,'connections':32,'rounds':args.rounds,'duration_seconds':args.duration,'cases':[name for name,_,_ in cases]},'samples':[]}
    peer=PeerServer(('127.0.0.1',0),Peer);threading.Thread(target=peer.serve_forever,daemon=True).start()
    sequence=[(round_,name,count,path) for round_ in range(args.rounds) for name,count,path in cases]
    random.Random(63081).shuffle(sequence)
    try:
        with tempfile.TemporaryDirectory(prefix='neokit-async-bench-') as temporary:
            directory=Path(temporary);script=directory/'fast.lua';script.write_text('wrk.path="/healthz"\nwrk.headers["Accept-Encoding"]="identity"\n')
            for round_,name,count,path in sequence:
                number=port();environment={'NEOKIT_ASYNC_HTTP_PORT':str(peer.server_port),'NEOKIT_ASYNC_PG_PORT':str(args.pg_port)}
                with server([str(binary),str(number)],environment,number,directory/'server.log') as (process,_):
                    assert request(number,'/healthz')[0]==200;quiet(number)
                    stop=threading.Event();outcomes=[];lock=threading.Lock()
                    def background():
                        while not stop.is_set():
                            status,_,body=request(number,path)
                            if name=='network-waits':assert status==504 and body==b'Async render expired',(status,body[:100])
                            else:assert status==200 and b'Database &lt;row&gt;' in body and b'Network &lt;slow&gt;' in body,(status,body[-200:])
                            with lock:outcomes.append(status)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,count)) as pool:
                        pending=[pool.submit(background) for _ in range(count)]
                        try:
                            time.sleep(.2);wrk(number,script,32,1)
                            with Resources(process) as resource:sample=wrk(number,script,32,args.duration)
                        finally:
                            stop.set()
                            for future in pending:future.result(timeout=12)
                    requests=int(re.search(r'(\d+) requests in',sample['raw'])[1]);sample.update(resource.metrics(requests))
                    sample.update({'round':round_,'case':name,'background_clients':count,'background_completions':len(outcomes),'requests':requests,'after':quiet(number)})
                    data['samples'].append(sample);(args.out/'results.json').write_text(json.dumps(data,indent=2)+'\n')
                    print(name,round_,round(sample['requests_per_sec'],1),'fast req/s',round(sample['p99_ms'],3),'p99 ms',round(sample['peak_sampled_rss_kib']/1024,1),'MiB',flush=True)
        lines=['# Async mixed-load measurements','',f'One NeoKit worker, production prepared libraries, 32 fast-route connections and one wrk thread. {args.rounds} shuffled rounds; one second warmup then {args.duration} seconds timing. Independent delayed HTTP peers and real PostgreSQL SCRAM queries (`pg_sleep(0.6)`). Background clients reopen their route continuously.','',
               '| Background work | Clients | Fast req/s (median) | Fast p99 ms | Peak sampled RSS MiB | Total server CPU % one core |','|---|---:|---:|---:|---:|---:|']
        for name,_,_ in cases:
            rows=[row for row in data['samples'] if row['case']==name];mid=lambda key:statistics.median(row[key] for row in rows)
            lines.append(f"| {name} | {int(mid('background_clients'))} | {mid('requests_per_sec'):,.1f} | {mid('p99_ms'):.3f} | {mid('peak_sampled_rss_kib')/1024:.1f} | {mid('cpu_percent_one_core'):.2f} |")
        lines+=['','CPU includes fast responses and all concurrent loader/stream work; it is not CPU attributable only to the fast route. PostgreSQL and peer-process resources are excluded from NeoKit RSS/CPU. RSS is sampled every 250 ms. All routes validate status/body, drain to zero live tasks/capture bytes/result bytes after each trial, and reject wrk errors. This is a busy shared Mac, not a universal capacity claim. [Raw measurements](results.json).','']
        (args.out/'REPORT.md').write_text('\n'.join(lines))
    finally:peer.shutdown();peer.server_close()

if __name__=='__main__':main()
