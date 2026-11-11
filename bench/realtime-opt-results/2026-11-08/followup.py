"""Run only after the final parity matrix; preserve the mixed blog result."""
from pathlib import Path
import asyncio,hashlib,json,random,resource,subprocess,sys,tempfile,time
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from bench.realtime import live_trial
from bench.parity_performance import hardware
out=Path(__file__).resolve().parent
end=time.monotonic()+1800
while not (out/'parity/REPORT.md').exists():
    if time.monotonic()>end:raise TimeoutError('Final matrix did not complete')
    time.sleep(.5)
command=[sys.executable,str(ROOT/'bench/regression_performance.py'),'--out',str(out/'blog-followup'),'--before',str(ROOT/'build/realtime-opt-before'),'--after',str(ROOT/'build/realtime-opt-final'),'--cases','blog','--rounds','6','--duration','10']
with (out/'blog-followup-run.log').open('w') as log:subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE);resource.setrlimit(resource.RLIMIT_NOFILE,(max(soft,min(4096,hard)),hard))
binary=ROOT/'build/realtime-opt-final/saas/server';data={'machine':hardware(),'configuration':{'rounds':3,'duration_seconds':4,'workers':1,'ssr_connections':1,'path':'/customers?records=10000'},'binary':{'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()},'samples':[]}
sequence=[(r,c) for r in range(3) for c in (1,100,1000)];random.Random(5307).shuffle(sequence)
with tempfile.TemporaryDirectory(prefix='neokit-final-live-') as temporary:
 for r,c in sequence:
  sample=asyncio.run(live_trial(binary,c,4,Path(temporary)));sample['round']=r;data['samples'].append(sample)
  (out/'mixed-live.json').write_text(json.dumps(data,indent=2)+'\n');print(r,c,sample['requests_per_sec'],sample['event_p99_ms'],flush=True)
print('PASS final mixed SaaS SSR/SSE',flush=True)
