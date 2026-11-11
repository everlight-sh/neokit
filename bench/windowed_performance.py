"""SSR delivery for equal logical data, deliberately different DOM strategies."""
import hashlib,html,json,random,re,shutil,statistics,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.http_smoke import running,request,ROOT
from bench.parity_performance import wrk,hardware

def main():
    out=ROOT/'bench/browser-results/2026-11-07-windowed';out.mkdir(parents=True,exist_ok=True)
    binaries={'before':ROOT/'build/performance/stress-before/server','windowed':ROOT/'build/studio/server'}
    data={'profile':'1000/10000 logical records; before all HTML rows, after 32 HTML rows plus inert JSON. Payloads differ.',
          'machine':hardware(),'disk_free_bytes':shutil.disk_usage(ROOT).free,'rounds':3,'seconds':2,'samples':[],
          'binaries':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in binaries.items()}}
    plan=[(round_,name,count,connections) for round_ in range(3) for name in binaries for count in (1000,10000) for connections in (1,32)]
    random.Random(917).shuffle(plan)
    for index,(round_,name,count,connections) in enumerate(plan):
        with running(binaries[name]) as port:
            status,headers,body=request(port,f'/stress?n={count}')
            assert status==200 and headers['Cache-Control']=='no-store'
            assert body.count(b'<tr data-row-id=')==(count if name=='before' else 32)
            if name=='windowed':
                raw=re.search(rb'<template data-web-data="stress">(.*?)</template>',body)[1]
                values=json.loads(html.unescape(raw.decode()));assert values['count']==count
                assert values['values']==[i*17%1000 for i in range(1,count+1)]
                time.sleep(.01)
                next_body=request(port,f'/stress?n={count}')[2]
                fresh=json.loads(html.unescape(re.search(rb'<template data-web-data="stress">(.*?)</template>',next_body)[1].decode()))
                assert fresh['stamp']>values['stamp']
            else:
                ids=re.findall(rb'<tr data-row-id="(\d+)"',body)
                assert list(map(int,ids))==list(range(1,count+1))
            script=out/'requests.lua';script.write_text(f'wrk.path="/stress?n={count}"\nwrk.headers["Accept-Encoding"]="identity"\n')
            wrk(port,script,connections,1);sample=wrk(port,script,connections,2)
            sample.update({'round':round_,'framework':name,'count':count,'connections':connections,'bytes':len(body)})
            data['samples'].append(sample);(out/'ssr.json').write_text(json.dumps(data,indent=2)+'\n')
            print(f'{index+1}/{len(plan)} {name} {count} c{connections}: {sample["requests_per_sec"]:.1f} req/s',flush=True)
    script.unlink()
    lines=['# List SSR delivery measurements','','Equal logical data, different rendering strategies: full HTML versus a bounded SSR window plus inert JSON. This is a product-level delivery comparison, not equal-DOM rendering throughput.','',
           'Three shuffled 2-second trials after a 1-second warmup, one native worker and one wrk thread, identity encoding. All requests independently validate record count/data; windowed freshness is checked.','',
           '| Records | Connections | Strategy | Median req/s | Response bytes | Median p99 ms |','|---|---:|---|---:|---:|---:|']
    for count in (1000,10000):
        for connections in (1,32):
            for name in binaries:
                selected=[s for s in data['samples'] if (s['count'],s['connections'],s['framework'])==(count,connections,name)]
                lines.append(f'| {count} | {connections} | {name} | {statistics.median(s["requests_per_sec"] for s in selected):.1f} | {selected[0]["bytes"]} | {statistics.median(s["p99_ms"] for s in selected):.3f} |')
    (out/'SSR.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':main()
