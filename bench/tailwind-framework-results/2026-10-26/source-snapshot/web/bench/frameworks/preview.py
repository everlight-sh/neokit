#!/usr/bin/env python3
"""Keep production baselines open for browser verification, outside measurements."""
import json
import os
import signal
import subprocess
import time

from adapters import BUILD,ENGINES,prepare_deployment,start_spec
from model import verify_server


def main():
    BUILD.mkdir(parents=True,exist_ok=True)
    (BUILD/'preview.pid').write_text(str(os.getpid()))
    processes=[];logs=[];results={}
    def interrupted(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:
        for i,engine in enumerate(ENGINES):
            deployment=prepare_deployment(engine)
            command,env=start_spec(deployment,9101+i)
            env={**env,'HOST':'0.0.0.0','HOSTNAME':'0.0.0.0'}
            log=(BUILD/('preview-'+engine+'.log')).open('w');logs.append(log)
            process=subprocess.Popen(['taskset','-c','0',*command],cwd=deployment['directory'],env=env,stdout=log,stderr=log,start_new_session=True);processes.append(process)
            import socket
            for _ in range(200):
                if process.poll() is not None:raise AssertionError((engine,process.returncode,(BUILD/('preview-'+engine+'.log')).read_text()))
                try:
                    with socket.create_connection(('127.0.0.1',9101+i),timeout=.1):break
                except OSError:time.sleep(.03)
            else:raise AssertionError(engine+' startup timeout')
            results[engine]={'port':9101+i,'verified_queries':verify_server(9101+i)}
            print('READY',engine,9101+i,flush=True)
        (BUILD/'preview-verification.json').write_text(json.dumps(results,indent=2)+'\n')
        while True:time.sleep(1)
    except KeyboardInterrupt:pass
    finally:
        for process in processes:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=10)
        for log in logs:log.close()
        (BUILD/'preview.pid').unlink(missing_ok=True)


if __name__=='__main__':main()
