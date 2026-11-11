"""Sample native authenticated rendering without changing the timed fixture."""
import argparse,json,os,subprocess,tempfile,time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bench.parity_performance import ROOT,server,port,token,validate_tasks
from bench.core_stats import snapshot

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    key='profile-test-session-key-at-least-32-bytes';signed,csrf=token(key);cookie='neokit_session='+signed
    with tempfile.TemporaryDirectory(prefix='neokit-auth-sample-') as temporary:
        directory=Path(temporary);number=port();origin=f'http://127.0.0.1:{number}'
        tasks=[{'id':f'bench-{i:03}','owner':'admin' if i<100 else 'other-user','title':f'Task {i}: <café> & "review"','done':i%3==0} for i in range(120)]
        (directory/'tasks.json').write_text(json.dumps({'schema':1,'tasks':tasks},separators=(',',':')))
        script=directory/'request.lua';script.write_text('wrk.headers["Cookie"]='+json.dumps(cookie)+'\nwrk.path="/dashboard"\n')
        with server([str(args.binary.resolve()),str(number)],{'NEOKIT_DATA_DIR':str(directory),'NEOKIT_SESSION_SECRET':key,'NEOKIT_ORIGIN':origin},number,args.out/'server.log') as (process,_):
            validate_tasks(number,cookie,csrf,origin,directory)
            with (args.out/'load.log').open('w') as output:
                load=subprocess.Popen(['wrk','-t1','-c32','-d7s','--latency','-s',str(script),f'http://127.0.0.1:{number}'],stdout=output,stderr=output)
                try:
                    time.sleep(1);group=snapshot(process.pid);pid=max(group,key=lambda pid:group[pid]['cpu_seconds'])
                    subprocess.run(['/usr/bin/sample',str(pid),'3','1','-file',str(args.out/'sample.txt')],check=True)
                finally:load.wait(timeout=12)
            validate_tasks(number,cookie,csrf,origin,directory)
        print(args.out/'sample.txt',flush=True)
if __name__=='__main__':main()
