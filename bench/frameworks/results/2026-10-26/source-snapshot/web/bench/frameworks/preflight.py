#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import subprocess
import sys

from adapters import BUILD,ENGINES,build_spec,prepare_deployment


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--engine',choices=ENGINES,action='append');parser.add_argument('--mode',choices=['ssr','static'],action='append');args=parser.parse_args()
    logs=Path('/results/preflight');logs.mkdir(parents=True,exist_ok=True);BUILD.mkdir(parents=True,exist_ok=True)
    deployments={}
    for engine in args.engine or ENGINES:
        for mode in args.mode or ('ssr','static'):
            command,cwd,_,env=build_spec(engine,mode)
            print('BUILD',engine,mode,flush=True)
            result=subprocess.run(['taskset','-c','0-3',*command],cwd=cwd,env=env,capture_output=True,text=True)
            (logs/(engine+'-'+mode+'.log')).write_text(result.stdout+'\n'+result.stderr)
            if result.returncode:
                print(result.stdout[-7000:]+result.stderr[-7000:],flush=True);raise SystemExit(result.returncode)
        if 'ssr' in (args.mode or ('ssr','static')):
            print('DEPLOY',engine,flush=True);deployments[engine]=prepare_deployment(engine)
    (BUILD/'deployments.json').write_text(json.dumps(deployments,indent=2)+'\n')
    print('PREFLIGHT COMPLETE',flush=True)


if __name__=='__main__':main()
