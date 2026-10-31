#!/usr/bin/env python3
"""Optional local browser check of the three already-built static exports."""
import argparse,functools,http.server,signal,threading
from adapters import ENGINES,export_directory

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=9101);a=p.parse_args()
    servers=[]
    try:
        for i,engine in enumerate(ENGINES):
            folder=export_directory(engine);assert (folder/'index.html').is_file(),folder
            handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(folder))
            server=http.server.ThreadingHTTPServer((a.host,a.port+i),handler);servers.append(server)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            print(engine,str(folder),'http://'+a.host+':'+str(a.port+i),flush=True)
        signal.signal(signal.SIGTERM,lambda *_:exit(0));signal.pause()
    finally:
        for server in servers:server.shutdown();server.server_close()
if __name__=='__main__':main()
