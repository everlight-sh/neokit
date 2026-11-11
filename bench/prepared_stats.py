"""Recheck the original authenticated prepared-library slowdown, before/after."""
import argparse,csv,hashlib,json,os,random,re,statistics,tempfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bench.core_stats import Resources
from bench.parity_performance import ROOT,port,server,token,validate_tasks,wrk

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--monolithic',type=Path,required=True)
    parser.add_argument('--regressed',type=Path,help='Optional saved async build before the optimization fix')
    parser.add_argument('--paired',action='store_true',help='Shuffle within each round to reduce host-load drift between builds')
    parser.add_argument('--rounds',type=int,default=5)
    parser.add_argument('--duration',type=int,default=5)
    args=parser.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
    binaries={'prepared-before':args.before.resolve(),'prepared-current':args.prepared.resolve(),'monolithic-current':args.monolithic.resolve()}
    if args.regressed:binaries['prepared-regressed']=args.regressed.resolve()
    data={'configuration':{'rounds':args.rounds,'duration_s':args.duration,'warmup_s':1,'connections':32,'workers':1,'tasks_owned':100,'tasks_other_owner':20,'paired':args.paired},
          'binaries':{name:{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size} for name,path in binaries.items()},'samples':[]}
    sequence=[];rng=random.Random(88412)
    for round_ in range(args.rounds):
        block=[(round_,mode) for mode in binaries]
        if args.paired:rng.shuffle(block)
        sequence.extend(block)
    if not args.paired:rng.shuffle(sequence)
    key='profile-test-session-key-at-least-32-bytes';signed,csrf=token(key);cookie='neokit_session='+signed
    with tempfile.TemporaryDirectory(prefix='neokit-prepared-stats-') as temporary:
        directory=Path(temporary)
        for index,(round_,mode) in enumerate(sequence):
            tasks=[{'id':f'bench-{i:03}','owner':'admin' if i<100 else 'other-user','title':f'Task {i}: <café> & "review"','done':i%3==0} for i in range(120)]
            (directory/'tasks.json').write_text(json.dumps({'schema':1,'tasks':tasks},separators=(',',':')))
            number=port();origin=f'http://127.0.0.1:{number}';environment={'NEOKIT_DATA_DIR':str(directory),'NEOKIT_SESSION_SECRET':key,'NEOKIT_ORIGIN':origin}
            with server([str(binaries[mode]),str(number)],environment,number,directory/'server.log') as (process,_):
                validate_tasks(number,cookie,csrf,origin,directory)
                script=directory/'requests.lua';script.write_text('wrk.headers["Cookie"]='+json.dumps(cookie)+'\nwrk.headers["Accept-Encoding"]="identity"\nwrk.path="/dashboard"\n')
                wrk(number,script,32,1)
                with Resources(process) as resource:sample=wrk(number,script,32,args.duration)
                requests=int(re.search(r'(\d+) requests in',sample['raw'])[1]);sample.update(resource.metrics(requests));sample.update({'round':round_,'mode':mode,'host_load_average':list(os.getloadavg())})
                validate_tasks(number,cookie,csrf,origin,directory);data['samples'].append(sample)
                (out/'prepared-results.json').write_text(json.dumps(data,indent=2)+'\n')
                print(f'{index+1}/{len(sequence)} {mode}: {sample["requests_per_sec"]:.1f} req/s, {sample["cpu_us_per_request"]:.2f} CPU us/req',flush=True)
    report(data,out)

def report(data,out):
    binaries=data['binaries'];configuration=data['configuration']
    values={mode:statistics.median(s['requests_per_sec'] for s in data['samples'] if s['mode']==mode) for mode in binaries}
    lines=['# Prepared-library authentication profile, before and after','',f'{configuration["rounds"]} shuffled {configuration["duration_s"]}-second trials per binary after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.','',
           '| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB | Binary MiB |','|---|---:|---:|---:|---:|']
    for mode in binaries:
        selected=[s for s in data['samples'] if s['mode']==mode]
        lines.append(f'| {mode} | {values[mode]:,.1f} | {statistics.median(s["cpu_us_per_request"] for s in selected):.2f} | {statistics.median(s["peak_sampled_rss_kib"] for s in selected)/1024:.1f} | {binaries[mode]["bytes"]/2**20:.2f} |')
    if configuration.get('paired'):
        lines+=['','Build order is shuffled within each round, rather than across all rounds, to reduce host-load drift. The host is shared; per-sample load averages are recorded.','',
                '| Round | Fixed vs pre-async throughput | Fixed vs regressed async throughput |','|---|---:|---:|']
        comparisons=[]
        for round_ in range(configuration['rounds']):
            rows={s['mode']:s for s in data['samples'] if s['round']==round_}
            before=(rows['prepared-current']['requests_per_sec']/rows['prepared-before']['requests_per_sec']-1)*100
            regressed=(rows['prepared-current']['requests_per_sec']/rows['prepared-regressed']['requests_per_sec']-1)*100 if 'prepared-regressed' in rows else None
            comparisons.append((before,regressed))
            lines.append(f'| {round_+1} | {before:+.1f}% | '+(f'{regressed:+.1f}%' if regressed is not None else '—')+' |')
        lines+=['',f'Median within-round throughput change versus pre-async: {statistics.median(row[0] for row in comparisons):+.1f}%.'+
                (f' Versus regressed async: {statistics.median(row[1] for row in comparisons):+.1f}%.' if comparisons[0][1] is not None else '')]
    change=(values['prepared-current']/values['prepared-before']-1)*100;gap=(values['prepared-current']/values['monolithic-current']-1)*100
    conclusion='These trials show no prepared throughput deficit; the small advantage over monolithic does not establish a general speed advantage.' if gap>=0 else f'The prepared build remains {abs(gap):.1f}% below monolithic in this run.'
    lines+=['',f'Current prepared throughput changes by {change:+.1f}% versus the saved prepared binary and {gap:+.1f}% versus the current monolithic binary. The saved and current builds use the same application workload and HTTP options. Compiler/runtime versions can differ; this comparison does not isolate one compiler optimization. Host noise and machine-code layout can still affect the measured difference.','',
       'The compiler preserves checked return-alias contracts and pre-optimization library bodies, including its constant-argument and small-pure-helper policy across prepared boundaries. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. ' + conclusion + ' Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).','']
    (out/'PREPARED.md').write_text('\n'.join(lines))

    fields=sorted(set().union(*(row.keys() for row in data['samples']))-{'raw'})
    with (out/'prepared-samples.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(data['samples'])

if __name__=='__main__':main()
