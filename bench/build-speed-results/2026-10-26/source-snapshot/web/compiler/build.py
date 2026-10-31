"""Discover a site, compile views/routes, bundle assets, and build native Axon."""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
import tempfile

from .templates import BuildError, NAME, compile_all, literal, parse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT/'examples/studio'


def compiler_env():
    compiler = Path(os.environ.get('AXON_HOME', ROOT.parent/'axon')).resolve()
    if not (compiler/'axonc/__main__.py').is_file():
        raise BuildError(f'Set AXON_HOME to an Axon compiler checkout; missing {compiler}')
    return {**os.environ, 'PYTHONPATH':str(compiler)+os.pathsep+os.environ.get('PYTHONPATH', '')}


def native_build(source, target, *, profile='release'):
    target.parent.mkdir(parents=True, exist_ok=True)
    env = compiler_env()
    # Generated routing/template trees benefit from a smaller forced-inlining
    # budget. Ordinary Axon programs retain the compiler's existing default.
    env.setdefault('AXON_INLINE_BUDGET', '100')
    command = ['python3', '-m', 'axonc', 'build', str(source), '-o', str(target),
               '--opt-level', '0' if profile == 'dev' else '3']
    cache = library_cache()
    if cache is not None:
        command += ['--library-cache', str(cache), '--library-root', str(ROOT/'src/lib.ax')]
    result = subprocess.run(command,
                            env=env, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise BuildError(result.stdout+'\n'+result.stderr)
    print(result.stdout.strip(), flush=True)


def library_cache():
    value = os.environ.get('AXON_LIBRARY_CACHE')
    if value == 'off': return None
    return Path(value).expanduser().resolve() if value else Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'axon-web/native'


def prepare():
    """Prepare framework/runtime code without compiling any application."""
    cache = library_cache()
    if cache is None: raise BuildError('AXON_LIBRARY_CACHE=off disables library preparation')
    cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='prepare-',dir=cache) as temporary:
        folder = Path(temporary)
        source = folder/'prepare.ax'
        source.write_text('import '+literal(str(ROOT/'src/lib.ax'))+'\neffect fn main() -> unit = noop()\n')
        native_build(source,folder/'prepare')
    print('Prepared checked Axon Web libraries: '+str(cache),flush=True)


def route_path(folder, base):
    parts = folder.relative_to(base).parts
    for part in parts:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+|\[[a-zA-Z_][a-zA-Z0-9_]*\]', part):
            raise BuildError(f'Unsupported route segment: {part}')
    dynamic = [p for p in parts if p.startswith('[')]
    if len(dynamic) > 1 or dynamic and not parts[-1].startswith('['):
        raise BuildError('v0.1 supports one dynamic parameter in the final path segment')
    return '/'+('/'.join(parts))


def discover(app):
    routes = []
    for config_path in sorted((app/'app').rglob('page.toml')):
        config = tomllib.loads(config_path.read_text())
        folder = config_path.parent
        route = route_path(folder, app/'app')
        if route == '/_web' or route.startswith('/_web/'):
            raise BuildError('The /_web/ namespace is reserved for built assets')
        key = 'Page_'+hashlib.sha256(route.encode()).hexdigest()[:10]
        if not NAME.fullmatch(config.get('loader', '')):
            raise BuildError(f'{config_path}: loader must name an Axon function')
        if config.get('mode', 'ssr') not in ('ssr', 'static'):
            raise BuildError(f'{config_path}: mode must be ssr or static')
        for flag in ('client','export'):
            if flag in config and not isinstance(config[flag],bool):
                raise BuildError(f'{config_path}: {flag} must be a boolean')
        template = parse(key, (folder/'page.view').read_text(), folder/'page.view')
        if template.props.get('title') != 'text':
            raise BuildError(f'{config_path}: pages require a title: text prop')
        dynamic = '[' in route
        paths = config.get('paths', [] if dynamic else [route])
        if not isinstance(paths,list) or any(not isinstance(path,str) for path in paths):
            raise BuildError(f'{config_path}: paths must be a list of URL strings')
        for path in paths:
            if not re.fullmatch(r'/[a-zA-Z0-9_/-]*', path) or '//' in path or path != '/' and path.endswith('/'):
                raise BuildError(f'{config_path}: export paths must be canonical URL paths')
            prefix = route[:route.index('[')] if dynamic else route
            if dynamic and (not path.startswith(prefix) or not path[len(prefix):] or '/' in path[len(prefix):]) or not dynamic and path != route:
                raise BuildError(f'{config_path}: path {path!r} does not match {route}')
        if config.get('mode') == 'static' and dynamic:
            raise BuildError('Dynamic routes can be snapshotted for export; static server routes must have fixed paths in v0.1')
        routes.append(dict(path=route,key=key,config=config,template=template,source=folder/'page.ax',paths=paths,dynamic=dynamic))
    if not routes:
        raise BuildError('No app/**/page.toml routes found')
    routes.sort(key=lambda r:(r['dynamic'],r['path']))
    return routes


def bundle_assets(app, out):
    directory = out/'assets'
    directory.mkdir(parents=True, exist_ok=True)
    assets = {}
    entry = app/'client/index.js' if (app/'client/index.js').is_file() else ROOT/'client/index.js'
    if entry.is_file():
        subprocess.run(['bun', 'build', str(entry), '--target=browser', '--format=esm',
                        '--splitting', '--minify', '--reject-unresolved', '--entry-naming=client-[hash].js',
                        '--chunk-naming=chunk-[hash].js', '--outdir='+str(directory)], check=True)
        entries = list(directory.glob('client-*.js'))
        if len(entries) != 1:
            raise BuildError('Expected one browser client entry')
        assets['client.js'] = '/_web/'+entries[0].name
    for file in sorted((app/'styles').glob('*.css')):
        name = file.stem+'-'+hashlib.sha256(file.read_bytes()).hexdigest()[:12]+'.css'
        shutil.copyfile(file, directory/name)
        assets[file.name] = '/_web/'+name
    files = {'/_web/'+p.name:p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}
    public = app/'public'
    for file in sorted(public.rglob('*')) if public.exists() else []:
        if file.is_symlink():
            raise BuildError('Public symlinks are not supported')
        if file.is_file():
            url = '/'+file.relative_to(public).as_posix()
            if url.startswith('/_web/'):
                raise BuildError('The /_web/ namespace is reserved')
            files[url] = file.read_bytes()
    return assets, files


def route_condition(route):
    return 'web_segment(request.path,'+literal(route['path'].split('[')[0])+').present' if route['dynamic'] else 'str_equal(request.path,'+literal(route['path'])+')'


def route_request(route, expression='request'):
    if not route['dynamic']:
        return expression
    return expression+' with { param=web_segment('+expression+'.path,'+literal(route['path'].split('[')[0])+').value }'


def build(app=DEFAULT_APP, out=None, *, target='all', profile='release'):
    if target not in ('server', 'static', 'all'):
        raise BuildError('Build target must be server, static or all')
    if profile not in ('release','dev'):
        raise BuildError('Build profile must be release or dev')
    app = Path(app).resolve()
    if not (app/'web.toml').is_file():
        raise BuildError(f'No web.toml in {app}')
    out = Path(out or ROOT/'build/studio').resolve()
    if out == app or out in app.parents or out == ROOT or out in ROOT.parents:
        raise BuildError('Build output must not replace source directories')
    for source in (app, ROOT/'src', ROOT/'compiler', ROOT/'client', ROOT/'tests'):
        if source == out or source in out.parents:
            raise BuildError('Build output must be outside source directories')
    if out.exists() and any(out.iterdir()) and not (out/'.axon-web-build').is_file():
        raise BuildError('Build output must be empty or an existing Axon Web build')
    out.mkdir(parents=True, exist_ok=True)
    (out/'.axon-web-build').write_text('axon-web v0.1\n')
    # Publish only this build's assets; old hashed entries must not leak into dispatch.
    if (out/'assets').exists():
        shutil.rmtree(out/'assets')
    generated = out/'generated'
    generated.mkdir(exist_ok=True)
    routes = discover(app)
    assets, files = bundle_assets(app, out)
    components = {p.stem:parse(p.stem,p.read_text(),p) for p in sorted((app/'components').glob('*.view'))}
    if any(not NAME.fullmatch(name) or name.startswith('Page_') or name == 'Layout' for name in components):
        raise BuildError('Invalid or reserved component name')
    layout = parse('Layout', (app/'app/layout.view').read_text(), app/'app/layout.view')
    if layout.props != {'title':'text','content':'html','client':'bool','mode':'text'}:
        raise BuildError('layout.view props must be title:text, content:html, client:bool, mode:text')
    templates = [*components.values(),*(r['template'] for r in routes),layout]
    code = ['import '+literal(str(ROOT/'src/lib.ax'))]
    for route in routes:
        code.append('import '+literal(str(route['source'])))
    api_routes = []
    for path in sorted((app/'app').rglob('route.toml')):
        config = tomllib.loads(path.read_text())
        url = route_path(path.parent, app/'app')
        if '[' in url or not NAME.fullmatch(config.get('handler','')):
            raise BuildError('API routes require a fixed path and named Axon handler')
        code.append('import '+literal(str(path.parent/'route.ax')))
        api_routes.append((url,config['handler']))
    all_paths = [r['path'] for r in routes]+[url for url,_ in api_routes]+list(files)
    if len(set(all_paths)) != len(all_paths):
        raise BuildError('A page/API/asset route is duplicated')
    code.append(compile_all(templates, components, assets))
    for route in routes:
        props = route['template'].props
        args = ','.join('props.'+name for name in props)
        key = route['key']
        code.append(f'fn body_{key}(request: WebRequest) -> WebHtml = let props={route["config"]["loader"]}(request) in view_{key}({args})')
        client = 'true' if route['config'].get('client', False) else 'false'
        code.append(f'fn render_{key}(request: WebRequest, fragment: bool) -> WebHtml = let props={route["config"]["loader"]}(request) in let body=view_{key}({args}) in if fragment then body else view_Layout(props.title,body,{client},if request.server then "native SSR" else "static export")')
    (generated/'common.ax').write_text('\n'.join(code)+'\n')
    export_routes = [(r,p) for r in routes if r['config'].get('export', True) for p in r['paths']]
    if len({p for _,p in export_routes}) != len(export_routes):
        raise BuildError('Export paths overlap')
    if any(r['config'].get('mode') == 'static' and not r['config'].get('export', True) for r in routes):
        raise BuildError('Static server routes must have export=true')
    available_export_paths = [path for _,path in export_routes]
    if target == 'server':
        # Only prerender pages the server actually embeds. SSR pages are
        # evaluated on request; compiling a second executable for them wastes
        # a whole native compilation. `web export` builds its own renderer.
        export_routes = [(r,p) for r,p in export_routes if r['config'].get('mode') == 'static']
    render_code = ['import "common.ax"', 'effect(fs) fn main(index: i64) -> unit = let body = ']
    for index,(route,path) in enumerate(export_routes):
        request = 'web_static_request('+literal(path)+')'
        if route['dynamic']:
            request = '('+request+') with { param='+literal(path[len(route['path'].split('[')[0]):])+' }'
        render_code.append(f'if index=={index} then render_{route["key"]}({request},false).bytes else ')
    render_code.append('"" in let written=print_out(body) in noop()')
    (generated/'render.ax').write_text('\n'.join(render_code)+'\n')
    snapshots = {}
    if export_routes or target != 'server':
        native_build(generated/'render.ax',out/'render',profile=profile)
        for index,(route,path) in enumerate(export_routes):
            snapshots[path] = subprocess.check_output([str(out/'render'),str(index)])
    server = ['import "common.ax"', 'fn web_dispatch(request: WebRequest, fragment: bool) -> WebResponse =']
    for url,data in files.items():
        ctype = 'text/javascript; charset=utf-8' if url.endswith('.js') else mimetypes.guess_type(url)[0] or 'application/octet-stream'
        server.append('if str_equal(request.path,'+literal(url)+') then (web_reply(200,'+literal(data)+','+literal(ctype)+') with { immutable='+('true' if url.startswith('/_web/') else 'false')+' }) else')
    for route in routes:
        condition = route_condition(route)
        if route['config'].get('mode') == 'static' and target != 'static':
            if route['path'] not in snapshots:
                raise BuildError('Static server routes must have export=true')
            body = literal(snapshots[route['path']])
            response = 'web_reply(200,'+body+',"text/html; charset=utf-8")'
        else:
            response = 'web_document(render_'+route['key']+'('+route_request(route)+',fragment))'
        server.append('if '+condition+' then '+response+' else')
    for url,handler in api_routes:
        server.append('if str_equal(request.path,'+literal(url)+') then '+handler+'(request) else')
    server.append('web_reply(404,"Not Found","text/plain; charset=utf-8")')
    server.extend([
        'effect(time) fn web_handle(raw: Arr u8 n) -> Arr u8 _ = let clock=time_unix_ms() in let request=web_request(raw,clock.value) in let head=str_equal(request.method,"HEAD") in if !head && !str_equal(request.method,"GET") then web_encode(web_reply(405,"Method Not Allowed","text/plain; charset=utf-8"),false) else web_encode(web_dispatch(request,str_equal(web_header(raw,"x-web-fragment"),"1")),head)',
        'effect fn main(port: i64{port>0,port<65536}) -> i64 = serve_effect(port,web_handle)',
    ])
    (generated/'server.ax').write_text('\n'.join(server)+'\n')
    if target != 'static':
        native_build(generated/'server.ax',out/'server',profile=profile)
    snapshot_dir = out/'snapshots'
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    for path,data in snapshots.items():
        destination = snapshot_dir/path.lstrip('/')/'index.html'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    for url,data in files.items():
        destination = snapshot_dir/url.lstrip('/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    omitted = [r['path'] for r in routes if not r['config'].get('export',True)]+[url for url,_ in api_routes]
    manifest = {'app':str(app),'routes':[{'path':r['path'],'mode':r['config'].get('mode','ssr'),'client':r['config'].get('client',False),'export':r['config'].get('export',True),'paths':r['paths']} for r in routes],
                'assets':assets,'files':{url:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for url,data in files.items()},'exported_paths':list(snapshots),'available_export_paths':available_export_paths,'omitted_server_routes':omitted,
                'build_target':target,'profile':profile,'server_bytes':(out/'server').stat().st_size if target != 'static' else None,'browser_js_bytes':sum(len(data) for url,data in files.items() if url.endswith('.js'))}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return out, manifest
