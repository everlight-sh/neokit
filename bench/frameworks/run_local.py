#!/usr/bin/env python3
"""Run the pinned suite in an isolated native Linux Docker container."""
import argparse,json,os,platform,shutil,subprocess,tarfile,tempfile,time,uuid
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
IGNORED={'.git','node_modules','__pycache__','build','.next','.astro','dist-ssr','dist-static','out','results','build-speed-results','tailwind-results','.neo-build'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--neo-home','--axon-home',dest='axon_home',type=Path,default=Path(os.environ.get('NEO_HOME',ROOT.parent/'neo')))
    p.add_argument('--context',default='desktop-linux');p.add_argument('--image',default='axon-web-framework-bench-tools:latest')
    p.add_argument('--skip-image-build',action='store_true');p.add_argument('--out',type=Path,required=True)
    p.add_argument('--build-speed',action='store_true',help='compare clean/warm Axon and Astro builds, including library preparation')
    p.add_argument('--rounds',type=int,default=5);p.add_argument('--build-rounds',type=int,default=3);p.add_argument('--duration',type=float,default=4)
    a=p.parse_args();out=a.out.resolve();assert not out.exists(),out;out.mkdir(parents=True)
    if shutil.disk_usage(ROOT).free<4*2**30:raise SystemExit('Need at least 4 GiB free host disk before building tools/dependencies')
    docker=['docker','--context',a.context];run=lambda *x,**kw:subprocess.run([*docker,*map(str,x)],check=True,**kw)
    if not a.skip_image_build:run('build','-f',HERE/'Dockerfile','-t',a.image,ROOT)
    image=subprocess.check_output([*docker,'image','inspect','--format','{{.Id}}',a.image],text=True).strip()
    container='axon-web-bench-'+uuid.uuid4().hex[:10]
    try:
        run('run','-d','--name',container,'--cpuset-cpus','0-3','--memory','4g','--memory-swap','4g','-v',str(out)+':/results',a.image)
        with tempfile.TemporaryDirectory(prefix='axon-framework-sources-') as temporary:
            archive=Path(temporary)/'sources.tar'
            with tarfile.open(archive,'w') as tar:
                def filter_member(member):return None if any(x in IGNORED for x in Path(member.name).parts) else member
                tar.add(ROOT,arcname='axon-web',filter=filter_member)
                # Only compiler/build source is needed, never previous bench outputs.
                for name in ('neo','neoc','neo.toml','runtime','stdlib'):
                    source=a.axon_home.resolve()/name
                    if source.exists():tar.add(source,arcname='axon/'+name,filter=filter_member)
            run('cp',archive,container+':/tmp/sources.tar')
        run('exec',container,'tar','-xf','/tmp/sources.tar','-C','/work')
        run('exec',container,'rm','/tmp/sources.tar')
        run('exec',container,'python3','bench/frameworks/bootstrap.py')
        run('exec',container,'python3','bench/frameworks/test_harness.py')
        if a.build_speed:
            args=['exec',container,'python3','bench/astro_build_speed.py','--out','/results/run','--rounds',str(a.build_rounds),'--runtime-rounds',str(a.rounds)]
        else:
            args=['exec',container,'python3','bench/frameworks/run_bench.py','--out','/results/run','--tools-image',image,'--rounds',str(a.rounds),'--build-rounds',str(a.build_rounds),'--duration',str(a.duration)]
        process=subprocess.Popen([*docker,*args])
        while process.poll() is None:
            if shutil.disk_usage(ROOT).free<2*2**30:
                run('stop','--time','10',container);raise SystemExit('Stopped own benchmark container: host disk below 2 GiB')
            time.sleep(2)
        assert process.returncode==0,process.returncode
        host={'platform':platform.platform(),'cpu':platform.processor() or platform.machine(),'tools_image':image,'docker_context':a.context}
        if platform.system()=='Darwin':
            host['cpu']=subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'],text=True).strip()
            host['swap_after_run']=subprocess.check_output(['sysctl','vm.swapusage'],text=True).strip()
        (out/'run/host.json').write_text(json.dumps(host,indent=2)+'\n')
        if not a.build_speed:subprocess.run(['python3',str(HERE/'render_report.py'),str(out/'run')],check=True)
        print('Report:',out/'run/REPORT.md')
    finally:run('rm','-f',container)
if __name__=='__main__':main()
