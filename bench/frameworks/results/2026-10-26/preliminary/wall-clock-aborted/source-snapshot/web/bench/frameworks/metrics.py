"""Linux process-group CPU/RSS/PSS sampler; counts processes, not thread RSS."""
import os
from pathlib import Path
import threading
import time

HZ=os.sysconf('SC_CLK_TCK')


def group(pid):
    result=[]
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():continue
        try:
            fields=(directory/'stat').read_text().rsplit(')',1)[1].split()
            if int(fields[2])!=pid:continue
            cpu=(int(fields[11])+int(fields[12]))/HZ
            memory={}
            for line in (directory/'smaps_rollup').read_text().splitlines():
                key,_,value=line.partition(':')
                if key in ('Rss','Pss'):memory[key]=int(value.split()[0])*1024
            result.append({'pid':int(directory.name),'cpu_s':cpu,'rss':memory.get('Rss',0),'pss':memory.get('Pss',0)})
        except (OSError,ValueError,IndexError):continue
    return {'cpu_s':sum(p['cpu_s'] for p in result),'rss':sum(p['rss'] for p in result),'pss':sum(p['pss'] for p in result),'processes':len(result)}


class Sampler:
    def __init__(self,pid):
        self.pid=pid;self.samples=[];self.stopped=threading.Event()
        self.thread=threading.Thread(target=self.sample,daemon=True)
    def sample(self):
        while not self.stopped.is_set():
            self.samples.append({'time_ns':time.perf_counter_ns(),**group(self.pid)})
            self.stopped.wait(.05)
    def start(self):self.thread.start();return self
    def stop(self):
        self.stopped.set();self.thread.join()
        return {'peak_rss':max((s['rss'] for s in self.samples),default=0),'peak_pss':max((s['pss'] for s in self.samples),default=0),'max_processes':max((s['processes'] for s in self.samples),default=0)}


def settle(pid):
    previous=group(pid)['cpu_s'];stable=0;deadline=time.monotonic()+8
    while stable<2 and time.monotonic()<deadline:
        time.sleep(.1);current=group(pid)['cpu_s']
        stable=stable+1 if current==previous else 0
        previous=current
