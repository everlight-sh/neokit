import argparse
import functools
import getpass
import tempfile
import http.server
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from .build import ROOT, DEFAULT_APP, build, prepare, compiler_env, compiler_command, native_build, build_migration
from .templates import BuildError


def server(binary, port):
    return subprocess.Popen([str(binary),str(port)], start_new_session=True,
                            env={**os.environ,'NEO_THREADS':os.environ.get('NEO_THREADS','1')})


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
                          ('node_modules', '.git', '.neo-build', '.axon-build', '.neokit', '__pycache__')
                          and Path(directory)/name != app/'vendor/neokit-sdk']
            paths.extend(Path(directory)/name for name in files)
    paths.extend(p for p in (ROOT/'package.json', ROOT/'bun.lock') if p.is_file())
    return [(str(p),p.stat().st_mtime_ns) for p in sorted(paths)]


def main():
    parser = argparse.ArgumentParser(prog='neokit', description='NeoKit: native SSR and static sites with optional browser modules')
    sub = parser.add_subparsers(dest='command', required=True)
    current = Path.cwd()
    project = current if (current/'web.toml').is_file() else DEFAULT_APP
    output = project/'.neokit/build' if project != DEFAULT_APP else ROOT/'build/studio'
    export_output = project.with_name(project.name+'-export') if project != DEFAULT_APP else ROOT/'build/export'
    sub.add_parser('prepare',help='prepare reusable checked native framework libraries (no application code)')
    for name in ('build','serve','dev','export'):
        p = sub.add_parser(name)
        p.add_argument('--app',type=Path,default=project)
        p.add_argument('--out',type=Path,default=output)
        p.add_argument('--profile',choices=['release','dev'],default='dev' if name == 'dev' else 'release')
        if name in ('serve','dev'):
            p.add_argument('--port',type=int,default=8094)
            if name == 'serve':
                p.add_argument('--no-build',action='store_true')
        if name == 'export':
            p.add_argument('--dest',type=Path,default=export_output)
            p.add_argument('--strict',action='store_true',help='Reject an export that omits server-only routes')
    sub.add_parser('test')
    p=sub.add_parser('init');p.add_argument('path',type=Path)
    p.add_argument('--standalone',action='store_true',help='include a relocatable NeoKit SDK in the application')
    p=sub.add_parser('release',help='create a checksummed NeoKit SDK archive')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--neo-archive',type=Path,help='Neo 0.2.0 multi-platform native release archive')
    p.add_argument('--neo-sha256',help='expected SHA-256 of the Neo archive')
    sub.add_parser('doctor')
    p=sub.add_parser('revalidate');p.add_argument('--app',type=Path,default=DEFAULT_APP);p.add_argument('--out',type=Path,default=ROOT/'build/revalidation')
    sub.add_parser('password',help='hash a password using the shared native security library')
    p=sub.add_parser('migrate');p.add_argument('--app',type=Path,default=DEFAULT_APP);p.add_argument('--out',type=Path,default=ROOT/'build/migrations');p.add_argument('--build-only',action='store_true')
    p = sub.add_parser('preview')
    p.add_argument('--dir',type=Path,default=export_output)
    p.add_argument('--port',type=int,default=8095)
    args = parser.parse_args()
    try:
        if args.command=='init':
            from .scaffold import init
            target=init(args.path,ROOT,compiler_env(),standalone=args.standalone)
            print(f'Created {target}. '+('Run ./neokit dev from the project directory.' if args.standalone else f'Run ./neokit dev --app {target}'))
            return
        if args.command=='release':
            from .release import bundle
            print(json.dumps(bundle(args.out,ROOT,args.neo_archive,args.neo_sha256),indent=2))
            return
        if args.command=='revalidate':
            out,manifest=build(args.app.resolve(),args.out.resolve(),target='server')
            binary=manifest.get('revalidator_binary')
            if not binary:raise BuildError('Application has no stale_ttl routes')
            if len(os.environ.get('NEOKIT_REVALIDATE_TOKEN',''))<32:raise BuildError('Set NEOKIT_REVALIDATE_TOKEN to a random secret of at least 32 bytes')
            subprocess.run([binary],check=True);return
        if args.command=='password':
            password=getpass.getpass('Password: ')
            if password!=getpass.getpass('Confirm password: '):raise BuildError('Passwords do not match')
            binary=ROOT/'build/tools/password';binary.parent.mkdir(parents=True,exist_ok=True)
            native_build(ROOT/'tools/password.neo',binary)
            result=subprocess.run([str(binary)],input=password.encode(),capture_output=True,check=True)
            print(result.stdout.decode().strip());return
        if args.command=='migrate':
            binary=build_migration(args.app,args.out)
            if not args.build_only:
                directory=os.environ.get('NEOKIT_DATA_DIR')
                if directory:Path(directory).mkdir(parents=True,exist_ok=True)
                subprocess.run([str(binary)],check=True)
            return
        if args.command=='doctor':
            env=compiler_env()
            for command in ([*compiler_command(env),'version'],[sys.executable,'--version'],['bun','--version'],['clang','--version']):
                result=subprocess.run(command,env=env,capture_output=True,text=True,check=True);print(result.stdout.splitlines()[0])
            print('NeoKit toolchain OK')
            return
        if args.command == 'prepare':
            prepare()
            return
        if args.command == 'test':
            subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py'],cwd=ROOT,check=True)
            subprocess.run(['bun','test','./client'],cwd=ROOT,check=True)
            query_test=ROOT/'build/tools/query-builder'
            native_build(ROOT/'tests/query_builder.neo',query_test)
            subprocess.run([str(query_test)],cwd=ROOT,check=True)
            subprocess.run([sys.executable,'-m','tests.http_smoke'],cwd=ROOT,check=True)
            subprocess.run([sys.executable,'-m','tests.consumer_smoke'],cwd=ROOT,check=True)
            subprocess.run([sys.executable,'-m','tests.scaffold_smoke'],cwd=ROOT,check=True)
            for app in ('features','dashboard'):
                build(ROOT/'examples'/app,ROOT/'build'/app,target='server')
                subprocess.run([sys.executable,'-m','tests.'+app+'_smoke'],cwd=ROOT,check=True)
            return
        if args.command == 'preview':
            directory = args.dir.resolve()
            if not (directory/'index.html').is_file():
                raise BuildError('Run ./neokit export first')
            handler = functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(directory))
            print(f'Static export preview: http://127.0.0.1:{args.port}',flush=True)
            http.server.ThreadingHTTPServer(('127.0.0.1',args.port),handler).serve_forever()
            return
        app = args.app.resolve()
        out = args.out.resolve()
        if args.command == 'serve' and args.no_build:
            if not (out/'server').is_file():
                raise BuildError('Run ./neokit build first')
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
            previous_export = any((dest/name).is_file() for name in ('.neokit-export', '.axon-web-export'))
            if dest.exists() and any(dest.iterdir()) and not previous_export:
                raise BuildError('Export destination must be empty or a previous NeoKit export')
            dest.mkdir(parents=True,exist_ok=True)
            if previous_export:
                shutil.rmtree(dest)
            shutil.copytree(out/'snapshots',dest,dirs_exist_ok=True)
            (dest/'.neokit-export').write_text('neokit v0.1\n')
            public_manifest = {key:value for key,value in manifest.items() if key != 'app'}
            (dest/'export-manifest.json').write_text(json.dumps(public_manifest,indent=2)+'\n')
            print(f'Exported {len(manifest["exported_paths"])} pages to {dest}; local widgets work without a server.')
            if manifest['omitted_server_routes']:
                print('Server-only routes omitted: '+', '.join(manifest['omitted_server_routes']))
        else:
            if not 0 < args.port < 65536:
                raise BuildError('Port must be 1..65535')
            if args.command=='serve':
                binary = str(out/'server')
                env = {**os.environ,'NEO_THREADS':os.environ.get('NEO_THREADS','1')}
                print(f'Native Neo SSR: http://127.0.0.1:{args.port}',flush=True)
                os.execve(binary,[binary,str(args.port)],env)
            os.environ['NEOKIT_DEV_STATUS']=str(out/'dev-status.json')
            (out/'dev-status.json').write_text(json.dumps({'revision':time.time_ns(),'error':None}))
            process = server(out/'server',args.port)
            print(f'Native Neo SSR: http://127.0.0.1:{args.port}',flush=True)
            try:
                previous = fingerprint(app)
                print('Watching sources; successful builds refresh connected development browsers.',flush=True)
                while process.poll() is None:
                    time.sleep(.5)
                    current = fingerprint(app)
                    if current != previous:
                        changed={path for path,stamp in current if dict(previous).get(path)!=stamp}
                        previous = current
                        try:
                            build(app,out,target='server',profile=args.profile)
                        except (BuildError,subprocess.CalledProcessError) as error:
                            (out/'dev-status.json').write_text(json.dumps({'revision':0,'error':str(error)}))
                            print(error,file=sys.stderr)
                            continue
                        manifest=json.loads((out/'manifest.json').read_text())
                        kind='client' if changed and all('/client/' in path for path in changed) else 'reload'
                        (out/'dev-status.json').write_text(json.dumps({'revision':time.time_ns(),'error':None,'kind':kind,'entry':manifest['assets'].get('client.js')}))
                        stop(process)
                        process = server(out/'server',args.port)
            finally:
                stop(process)
    except KeyboardInterrupt:
        pass
    except (BuildError,subprocess.CalledProcessError,OSError) as error:
        print('neokit: '+str(error),file=sys.stderr)
        raise SystemExit(1)
