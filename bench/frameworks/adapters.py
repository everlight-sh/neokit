"""Production build/start commands and relocatable deployment artifacts."""
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
BUILD=ROOT/'build/frameworks'
ENGINES=('Axon','Astro','Next')
ENV={**os.environ,'NODE_ENV':'production','NEXT_TELEMETRY_DISABLED':'1','ASTRO_TELEMETRY_DISABLED':'1','NEO_THREADS':'1','UV_THREADPOOL_SIZE':'4'}


def framework_cli(package,name):
    base=HERE/'node_modules'/package
    bins=json.loads((base/'package.json').read_text())['bin']
    return str(base/(bins if isinstance(bins,str) else bins[name]))


def build_spec(engine,mode):
    if engine=='Axon':
        target=BUILD/('axon-'+mode)
        cmd=[str(ROOT/'web'),'export' if mode=='static' else 'build','--app',str(HERE/'apps/axon'),'--out',str(target)]
        if mode=='static':cmd+=['--dest',str(BUILD/'axon-export')]
        return cmd,ROOT,[target]+([BUILD/'axon-export'] if mode=='static' else []),ENV
    if engine=='Astro':
        project=HERE/'apps/astro'
        cmd=['node',framework_cli('astro','astro'),'build']
        return cmd,project,[project/('dist-static' if mode=='static' else 'dist-ssr'),project/'.astro'],{**ENV,'BENCH_MODE':mode}
    project=HERE/'apps/next'/('static-app' if mode=='static' else '')
    return ['node',framework_cli('next','next'),'build'],project,[project/'.next',project/'out'],{**ENV,'BENCH_MODE':mode}


def prepare_deployment(engine):
    destination=BUILD/'deploy'/engine.lower()
    if destination.exists():shutil.rmtree(destination)
    destination.mkdir(parents=True)
    if engine=='Axon':
        shutil.copy2(BUILD/'axon-ssr/server',destination/'server')
        return {'directory':str(destination),'entry':str(destination/'server'),'command':[str(destination/'server')],'env':ENV,'node':False}
    if engine=='Astro':
        project=HERE/'apps/astro'
        entry=project/'dist-ssr/server/entry.mjs'
        subprocess.run(['node',str(HERE/'artifacts.mjs'),str(HERE),str(entry),str(destination)],cwd=HERE,check=True)
        deployed=destination/'apps/astro/dist-ssr'
        shutil.copytree(project/'dist-ssr',deployed,dirs_exist_ok=True)
        return {'directory':str(destination),'entry':str(deployed/'server/entry.mjs'),'command':['node',str(deployed/'server/entry.mjs')],'env':{**ENV,'BENCH_MODE':'ssr'},'node':True}
    project=HERE/'apps/next'
    shutil.copytree(project/'.next/standalone',destination,dirs_exist_ok=True)
    entry=destination/'apps/next/server.js'
    if not entry.exists():
        candidates=[p for p in destination.rglob('server.js') if 'node_modules' not in p.parts]
        assert len(candidates)==1,candidates
        entry=candidates[0]
    shutil.copytree(project/'.next/static',entry.parent/'.next/static',dirs_exist_ok=True)
    if (project/'public').exists():shutil.copytree(project/'public',entry.parent/'public',dirs_exist_ok=True)
    return {'directory':str(destination),'entry':str(entry),'command':['node',str(entry)],'env':ENV,'node':True}


def export_directory(engine):
    return BUILD/'axon-export' if engine=='Axon' else HERE/'apps/astro/dist-static' if engine=='Astro' else HERE/'apps/next/static-app/out'


def start_spec(deployment,port):
    command=deployment['command']+([str(port)] if not deployment['node'] else [])
    return command,{**deployment['env'],'PORT':str(port),'HOST':'127.0.0.1','HOSTNAME':'127.0.0.1'}
