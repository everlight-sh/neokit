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

from .build import ROOT, DEFAULT_APP, build, prepare, compiler_env, compiler_command, native_build, build_migration, refresh_assets
from .templates import BuildError

# Longer suites and the applications each one serves from build/.
# tests.async_rendering also needs a PostgreSQL server; run it directly.
EXTENDED_SUITES = {
    'dev_css_smoke': (),
    'large_responses': (('tests/catalog_fixture', 'build/catalog'),),
    'realtime': (('tests/realtime_fixture', 'build/realtime/fixture'),),
    'showcase_smoke': tuple(('examples/'+name, 'build/showcase/'+name) for name in ('ssr-site', 'blog', 'saas')),
}


def server(binary, port, threads=None):
    env = dict(os.environ)
    if threads is not None:
        env.setdefault('NEO_THREADS', str(threads))
    return subprocess.Popen([str(binary),str(port)], start_new_session=True, env=env)


def stop(process, grace=5):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()


def fingerprint(app):
    stamps = {}
    # Tailwind may scan any text source; watch application files, including
    # imported CSS, without descending into installed dependency trees.
    for base in (app, ROOT/'src', ROOT/'compiler', ROOT/'client'):
        for directory, folders, files in os.walk(base):
            folders[:] = [name for name in folders if name not in
                          ('node_modules', '.git', '.neo-build', '.neokit', '__pycache__')
                          and Path(directory)/name != app/'vendor/neokit-sdk']
            for name in files:
                path = Path(directory)/name
                try:
                    stamps[str(path)] = path.stat().st_mtime_ns
                except FileNotFoundError:
                    pass
    for path in (ROOT/'package.json', ROOT/'bun.lock'):
        if path.is_file():
            stamps[str(path)] = path.stat().st_mtime_ns
    return stamps


def change_kind(app, changed):
    """'css' or 'client' when only browser sources changed, else 'reload'."""
    styles, client = app/'styles', (app/'client', ROOT/'client')
    paths = [Path(path) for path in changed]
    if not paths or any(path.name == 'dev.js' and path.parent == ROOT/'client' for path in paths):
        return 'reload'
    if all(path.is_relative_to(styles) for path in paths):
        return 'css'
    if all(path.is_relative_to(styles) or any(path.is_relative_to(folder) for folder in client) for path in paths):
        return 'client'
    return 'reload'


def write_status(out, **status):
    target = out/'dev-status.json'
    staged = target.with_name(target.name+'.next')
    staged.write_text(json.dumps({'revision':time.time_ns(),'error':None,**status}))
    os.replace(staged, target)


def develop(app, out, port, profile):
    os.environ['NEOKIT_DEV_STATUS'] = str(out/'dev-status.json')
    write_status(out)
    process = server(out/'server', port, threads=1)
    print(f'Native Neo SSR: http://127.0.0.1:{port}',flush=True)
    reported = False
    try:
        previous = fingerprint(app)
        print('Watching sources; successful builds refresh connected development browsers.',flush=True)
        while True:
            time.sleep(.25)
            if process is not None and process.poll() is not None and not reported:
                print(f'neokit: the development server exited with status {process.returncode}; '
                      'fix the problem and save a file to restart it.', file=sys.stderr, flush=True)
                reported = True
            current = fingerprint(app)
            if current == previous:
                continue
            changed = {path for path in current.keys() | previous.keys() if current.get(path) != previous.get(path)}
            previous = current
            kind = change_kind(app, changed)
            try:
                if kind != 'reload' and process is not None and process.poll() is None:
                    # The development server reads out/assets per request.
                    refresh_assets(app, out)
                else:
                    kind = 'reload'
                    build(app,out,target='server',profile=profile)
            except (BuildError,subprocess.CalledProcessError) as error:
                write_status(out, revision=0, error=str(error))
                print(error,file=sys.stderr,flush=True)
                continue
            manifest = json.loads((out/'manifest.json').read_text())
            if kind == 'reload':
                stop(process, grace=2)
                process = server(out/'server', port, threads=1)
                reported = False
            write_status(out, kind=kind, entry=manifest['assets'].get('client.js'))
    finally:
        stop(process, grace=2)


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
    p=sub.add_parser('test');p.add_argument('--extended',action='store_true',help='also run the long, browser-free integration suites')
    p=sub.add_parser('init');p.add_argument('path',type=Path)
    p.add_argument('--standalone',action='store_true',help='include a relocatable NeoKit SDK in the application')
    p=sub.add_parser('release',help='create a checksummed NeoKit SDK archive')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--neo-archive',type=Path,help='Neo 0.2.5 multi-platform native release archive')
    p.add_argument('--neo-sha256',help='expected SHA-256 of the Neo archive')
    sub.add_parser('doctor')
    p=sub.add_parser('revalidate');p.add_argument('--app',type=Path,default=project);p.add_argument('--out',type=Path,default=output.with_name(output.name+'-revalidation'))
    sub.add_parser('password',help='hash a password using the shared native security library')
    p=sub.add_parser('migrate');p.add_argument('--app',type=Path,default=project);p.add_argument('--out',type=Path,default=output.with_name(output.name+'-migrations'));p.add_argument('--build-only',action='store_true')
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
            subprocess.run([sys.executable,'-m','tests.audit_features_smoke'],cwd=ROOT,check=True)
            for app in ('features','dashboard'):
                build(ROOT/'examples'/app,ROOT/'build'/app,target='server')
                subprocess.run([sys.executable,'-m','tests.'+app+'_smoke'],cwd=ROOT,check=True)
            if args.extended:
                for suite,apps in EXTENDED_SUITES.items():
                    for source,destination in apps:
                        build(ROOT/source,ROOT/destination,target='server')
                    subprocess.run([sys.executable,'-m','tests.'+suite],cwd=ROOT,check=True)
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
            previous_export = any((dest/name).is_file() for name in ('.neokit-export',))
            if dest.exists() and any(dest.iterdir()) and not previous_export:
                raise BuildError('Export destination must be empty or a previous NeoKit export')
            # Copy beside the destination, then swap, so a failed copy keeps the previous export.
            dest.parent.mkdir(parents=True,exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix='.'+dest.name+'-',dir=dest.parent))
            try:
                shutil.copytree(out/'snapshots',staged,dirs_exist_ok=True)
                (staged/'.neokit-export').write_text('neokit v0.1\n')
                public_manifest = {key:value for key,value in manifest.items() if key != 'app'}
                (staged/'export-manifest.json').write_text(json.dumps(public_manifest,indent=2)+'\n')
                if dest.exists():
                    shutil.rmtree(dest)
                os.replace(staged,dest)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
            print(f'Exported {len(manifest["exported_paths"])} pages to {dest}; local widgets work without a server.')
            if manifest['omitted_server_routes']:
                print('Server-only routes omitted: '+', '.join(manifest['omitted_server_routes']))
        else:
            if not 0 < args.port < 65536:
                raise BuildError('Port must be 1..65535')
            if args.command=='serve':
                binary = str(out/'server')
                # Production serving uses every CPU unless NEO_THREADS says otherwise.
                print(f'Native Neo SSR: http://127.0.0.1:{args.port}',flush=True)
                os.execve(binary,[binary,str(args.port)],dict(os.environ))
            develop(app, out, args.port, args.profile)
    except KeyboardInterrupt:
        pass
    except (BuildError,subprocess.CalledProcessError,OSError) as error:
        print('neokit: '+str(error),file=sys.stderr)
        raise SystemExit(1)
