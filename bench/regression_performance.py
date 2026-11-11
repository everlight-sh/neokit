"""Paired native SSR regression probes, including authenticated durable state."""
import argparse,hashlib,json,os,random,re,statistics,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bench.parity_performance import server,port,request,wrk,token,validate_tasks,hardware
from bench.core_stats import Resources

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--before',type=Path,required=True);parser.add_argument('--after',type=Path,required=True);parser.add_argument('--rounds',type=int,default=5);parser.add_argument('--duration',type=int,default=6);parser.add_argument('--cases',nargs='+',choices=['ssr-site','blog','saas','dashboard']);args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    cases=[('ssr-site','/'),('blog','/'),('saas','/customers?records=10000'),('dashboard','/dashboard')]
    if args.cases:cases=[case for case in cases if case[0] in args.cases]
    data={'configuration':{'rounds':args.rounds,'duration':args.duration,'connections':32,'workers':1,'paired':True},'machine':hardware(),'identities':{},'samples':[]}
    for phase,folder in [('before',args.before),('after',args.after)]:
        for name,path in cases:
            binary=(folder/name/'server').resolve();data['identities'][phase+'/'+name]={'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
    key='regression-test-session-key-at-least-32-bytes';signed,csrf=token(key);cookie='neokit_session='+signed
    rng=random.Random(98271)
    with tempfile.TemporaryDirectory(prefix='neokit-regression-') as temporary:
        folder=Path(temporary)
        for round_ in range(args.rounds):
            order=list(cases);rng.shuffle(order)
            for name,path in order:
                phases=['before','after'];rng.shuffle(phases)
                for phase in phases:
                    number=port();environment={};origin=f'http://127.0.0.1:{number}'
                    if name=='dashboard':
                        tasks=[{'id':f'bench-{i:03}','owner':'admin' if i<100 else 'other-user','title':f'Task {i}: <café> & "review"','done':i%3==0} for i in range(120)]
                        (folder/'tasks.json').write_text(json.dumps({'schema':1,'tasks':tasks},separators=(',',':')))
                        environment={'NEOKIT_DATA_DIR':str(folder),'NEOKIT_SESSION_SECRET':key,'NEOKIT_ORIGIN':origin}
                    binary=Path(data['identities'][phase+'/'+name]['path'])
                    with server([str(binary),str(number)],environment,number,folder/'server.log') as (process,_):
                        if name=='dashboard':validate_tasks(number,cookie,csrf,origin,folder)
                        else:
                            status,_,body=request(number,path);assert status==200 and b'<html' in body
                            if name=='saas':assert body.count(b'data-customer-id=')==25
                        script=folder/'request.lua';script.write_text('wrk.path='+json.dumps(path)+'\nwrk.headers["Accept-Encoding"]="identity"\n'+('wrk.headers["Cookie"]='+json.dumps(cookie)+'\n' if name=='dashboard' else ''))
                        wrk(number,script,32,1)
                        with Resources(process) as resources:sample=wrk(number,script,32,args.duration)
                        count=int(re.search(r'(\d+) requests in',sample['raw'])[1]);sample.update(resources.metrics(count));sample.update(round=round_,phase=phase,case=name,load_average=list(os.getloadavg()))
                        if name=='dashboard':validate_tasks(number,cookie,csrf,origin,folder)
                        else:assert request(number,path)[0]==200
                    data['samples'].append(sample);(args.out/'results.json').write_text(json.dumps(data,indent=2)+'\n');print(round_,name,phase,round(sample['requests_per_sec'],1),flush=True)
    summary=[]
    for name,path in cases:
        selected=lambda phase:[s for s in data['samples'] if s['case']==name and s['phase']==phase]
        before=statistics.median(s['requests_per_sec'] for s in selected('before'));after=statistics.median(s['requests_per_sec'] for s in selected('after'))
        changes=[]
        for r in range(args.rounds):
            values={s['phase']:s['requests_per_sec'] for s in data['samples'] if s['case']==name and s['round']==r};changes.append((values['after']/values['before']-1)*100)
        summary.append({'case':name,'before_rps':before,'after_rps':after,'median_change_percent':(after/before-1)*100,'paired_changes_percent':changes,'paired_median_change_percent':statistics.median(changes)})
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
if __name__=='__main__':main()
