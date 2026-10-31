import argparse
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from .build import ROOT, DEFAULT_APP, build, prepare
from .templates import BuildError


def server(binary, port):
    return subprocess.Popen([str(binary),str(port)], start_new_session=True,
                            env={**os.environ,'AXON_THREADS':os.environ.get('AXON_THREADS','1')})


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


def fingerprint(app):
    paths = []
    # Tailwind may scan any text source; watch application files, including
    # imported CSS, without descending into installed dependency trees.
    for base in (app, ROOT/'src', ROOT/'compiler', ROOT/'client'):
        for directory, folders, files in os.walk(base):
            folders[:] = [name for name in folders if name not in
                          ('node_modules', '.git', '.axon-build', '__pycache__')]
            paths.extend(Path(directory)/name for name in files)
    paths.extend(p for p in (ROOT/'package.json', ROOT/'bun.lock') if p.is_file())
    return [(str(p),p.stat().st_mtime_ns) for p in sorted(paths)]


def main():
    parser = argparse.ArgumentParser(description='Axon Web: native SSR and static sites with optional browser modules')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('prepare',help='prepare reusable checked native framework libraries (no application code)')
    for name in ('build','serve','dev','export'):
        p = sub.add_parser(name)
        p.add_argument('--app',type=Path,default=DEFAULT_APP)
        p.add_argument('--out',type=Path,default=ROOT/'build/studio')
        p.add_argument('--profile',choices=['release','dev'],default='dev' if name == 'dev' else 'release')
        if name in ('serve','dev'):
            p.add_argument('--port',type=int,default=8094)
            if name == 'serve':
                p.add_argument('--no-build',action='store_true')
        if name == 'export':
            p.add_argument('--dest',type=Path,default=ROOT/'build/export')
            p.add_argument('--strict',action='store_true',help='Reject an export that omits server-only routes')
    sub.add_parser('test')
    p = sub.add_parser('preview')
    p.add_argument('--dir',type=Path,default=ROOT/'build/export')
    p.add_argument('--port',type=int,default=8095)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare()
            return
        if args.command == 'test':
            subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py'],cwd=ROOT,check=True)
            subprocess.run(['bun','test','./client'],cwd=ROOT,check=True)
            subprocess.run([sys.executable,'tests/http_smoke.py'],cwd=ROOT,check=True)
            subprocess.run([sys.executable,'-m','tests.consumer_smoke'],cwd=ROOT,check=True)
            return
        if args.command == 'preview':
            directory = args.dir.resolve()
            if not (directory/'index.html').is_file():
                raise BuildError('Run ./web export first')
            handler = functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(directory))
            print(f'Static export preview: http://127.0.0.1:{args.port}',flush=True)
            http.server.ThreadingHTTPServer(('127.0.0.1',args.port),handler).serve_forever()
            return
        app = args.app.resolve()
        out = args.out.resolve()
        if args.command == 'serve' and args.no_build:
            if not (out/'server').is_file():
                raise BuildError('Run ./web build first')
        else:
            out, manifest = build(app,out,target='static' if args.command == 'export' else 'server',profile=args.profile)
        if args.command == 'build':
            print(f'Built {out}/server\nRoutes: {len(manifest["routes"])}; browser JS: {manifest["browser_js_bytes"]} bytes across split modules')
        elif args.command == 'export':
            if args.strict and manifest['omitted_server_routes']:
                raise BuildError('Server-only routes cannot be exported: '+', '.join(manifest['omitted_server_routes']))
            dest = args.dest.resolve()
            if dest == out or dest in out.parents or out in dest.parents or dest == ROOT or dest in ROOT.parents or app == dest or app in dest.parents:
                raise BuildError('Export destination must be separate from source/build directories')
            if dest.exists() and any(dest.iterdir()) and not (dest/'.axon-web-export').is_file():
                raise BuildError('Export destination must be empty or a previous Axon Web export')
            dest.mkdir(parents=True,exist_ok=True)
            if (dest/'.axon-web-export').exists():
                shutil.rmtree(dest)
            shutil.copytree(out/'snapshots',dest,dirs_exist_ok=True)
            (dest/'.axon-web-export').write_text('axon-web v0.1\n')
            public_manifest = {key:value for key,value in manifest.items() if key != 'app'}
            (dest/'export-manifest.json').write_text(json.dumps(public_manifest,indent=2)+'\n')
            print(f'Exported {len(manifest["exported_paths"])} pages to {dest}; local widgets work without a server.')
            if manifest['omitted_server_routes']:
                print('Server-only routes omitted: '+', '.join(manifest['omitted_server_routes']))
        else:
            if not 0 < args.port < 65536:
                raise BuildError('Port must be 1..65535')
            process = server(out/'server',args.port)
            print(f'Native Axon SSR: http://127.0.0.1:{args.port}',flush=True)
            try:
                if args.command == 'dev':
                    previous = fingerprint(app)
                    print('Watching sources; successful builds restart the server. Reload the browser after edits.',flush=True)
                    while process.poll() is None:
                        time.sleep(.5)
                        current = fingerprint(app)
                        if current != previous:
                            previous = current
                            try:
                                build(app,out,target='server',profile=args.profile)
                            except (BuildError,subprocess.CalledProcessError) as error:
                                print(error,file=sys.stderr)
                                continue
                            stop(process)
                            process = server(out/'server',args.port)
                else:
                    raise SystemExit(process.wait())
            finally:
                stop(process)
    except KeyboardInterrupt:
        pass
    except (BuildError,subprocess.CalledProcessError,OSError) as error:
        print('axon-web: '+str(error),file=sys.stderr)
        raise SystemExit(1)
