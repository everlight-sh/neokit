#!/usr/bin/env python3
"""Run the matched Tailwind suite; optionally retain its container for browser checks."""
import argparse,json,os,platform,shutil,subprocess,tarfile,tempfile,time,uuid
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
IGNORED={'.git','node_modules','__pycache__','build','.next','.astro','dist-ssr','dist-static','out','results','build-speed-results','tailwind-results','tailwind-framework-results','.neo-build'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--neo-home','--axon-home',dest='axon_home',type=Path,default=ROOT.parent/'axon')
    p.add_argument('--context',default='desktop-linux');p.add_argument('--image',default='axon-web-framework-bench-tools:latest')
    p.add_argument('--keep-container',action='store_true');p.add_argument('--rounds',type=int,default=3);p.add_argument('--build-rounds',type=int,default=3)
    a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    assert shutil.disk_usage(ROOT).free>4*2**30,'Need 4 GiB free'
    docker=['docker','--context',a.context]
    def run(*cmd,**kwargs):return subprocess.run([*docker,*map(str,cmd)],check=True,**kwargs)
    if subprocess.run([*docker,'image','inspect',a.image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        run('build','-f',HERE/'Dockerfile','-t',a.image,ROOT)
    image=subprocess.check_output([*docker,'image','inspect','--format','{{.Id}}',a.image],text=True).strip()
    container='axon-web-tailwind-compare-'+uuid.uuid4().hex[:8];success=False
    try:
        run('run','-d','--name',container,'--cpuset-cpus','0-3','--memory','4g','--memory-swap','4g',
            '-p','127.0.0.1:8121-8147:8121-8147','-v',str(out)+':/results',a.image)
        (out/'container.json').write_text(json.dumps({'name':container,'context':a.context,'tools_image':image},indent=2)+'\n')
        with tempfile.TemporaryDirectory(prefix='axon-tailwind-sources-') as temp:
            archive=Path(temp)/'sources.tar'
            def filtered(member):return None if any(x in IGNORED for x in Path(member.name).parts) else member
            with tarfile.open(archive,'w') as tar:
                tar.add(ROOT,arcname='axon-web',filter=filtered)
                for name in ('neo','neoc','neo.toml','runtime','stdlib'):
                    source=a.axon_home.resolve()/name
                    if source.exists():tar.add(source,arcname='axon/'+name,filter=filtered)
            run('cp',archive,container+':/tmp/sources.tar')
        run('exec',container,'tar','-xf','/tmp/sources.tar','-C','/work')
        run('exec',container,'rm','/tmp/sources.tar')
        run('exec',container,'bun','install','--frozen-lockfile')
        run('exec',container,'python3','bench/frameworks/bootstrap.py')
        run('exec',container,'python3','bench/frameworks/prepare_tailwind.py')
        run('exec',container,'python3','bench/frameworks/test_harness.py')
        begin=time.monotonic()
        with (out/'framework-prepare.log').open('w') as log:run('exec',container,'./web','prepare',stdout=log,stderr=subprocess.STDOUT)
        (out/'preparation.json').write_text(json.dumps({'framework_library_prepare_wall_s':time.monotonic()-begin,'includes_docker_exec_overhead':True,'dependency_installation_outside_timing':True},indent=2)+'\n')
        command=[*docker,'exec',container,'python3','bench/frameworks/run_bench.py','--out','/results/run','--rounds',str(a.rounds),'--build-rounds',str(a.build_rounds),'--duration','3','--connections','32','--tools-image',image]
        process=subprocess.Popen(command)
        while process.poll() is None:
            if shutil.disk_usage(ROOT).free<2*2**30:
                run('stop',container);raise RuntimeError('Stopped own benchmark: host free disk below 2 GiB')
            time.sleep(2)
        assert process.returncode==0,process.returncode
        host={'platform':platform.platform(),'tools_image':image,'docker_context':a.context,'memory_gib':4,'cpus':4,'swap_disabled_in_container':True}
        if platform.system()=='Darwin':
            host['cpu']=subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'],text=True).strip()
            host['swap_after_run']=subprocess.check_output(['sysctl','vm.swapusage'],text=True).strip()
        (out/'run/host.json').write_text(json.dumps(host,indent=2)+'\n')
        shutil.copy2(out/'preparation.json',out/'run/preparation.json');shutil.copy2(out/'framework-prepare.log',out/'run/framework-prepare.log')
        success=True;print('HTTP/build suite complete:',out/'run',flush=True)
        if a.keep_container:print('Retained for CUA browser measurements:',container,flush=True)
    finally:
        if not(success and a.keep_container):run('rm','-f',container)


if __name__=='__main__':main()
