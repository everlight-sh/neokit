"""Discover a site, compile views/routes, bundle assets, and build native Neo."""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import tempfile
import time

from .templates import BuildError, NAME, compile_all, literal, parse
from .routes import route_path, matches, precedence, shape, methods
from .assets import metadata, generated_assets, compressed
from .actions import action_adapter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT/'examples/studio'


def compiler_env():
    def native(root):
        return (root/'bin/neoc').is_file() or (root/'.neo-build/bin/neoc').is_file()

    launcher = shutil.which('neo')
    candidates = [ROOT.parent/'neo', ROOT.parent/'axon']
    if launcher:
        candidates.append(Path(launcher).resolve().parent)
    default = next((path for path in candidates if native(path)), ROOT.parent/'neo')
    compiler = Path(os.environ.get('NEO_HOME', default)).expanduser().resolve()
    if not native(compiler) or not (compiler/'neo').is_file():
        raise BuildError(f'Set NEO_HOME to an installed native Neo toolchain; checked {compiler}')
    env = {**os.environ, 'NEO_HOME':str(compiler), 'NEO_ROOT':str(compiler)}
    env.pop('NEOC', None)
    return env


def compiler_command(environment):
    return [str(Path(environment['NEO_HOME'])/'neo')]


def package_context(app):
    path=Path(app).resolve()
    return next((root for root in (path,*path.parents) if any((root/name).is_file() for name in ('neo.toml','axon.toml'))),ROOT)


def cache_identity(app):
    compiler=Path(compiler_env()['NEO_HOME'])
    digest=hashlib.sha256()
    bases=dict.fromkeys((package_context(app),app,ROOT/'src',ROOT/'compiler',compiler/'stdlib',compiler/'runtime'))
    for directory in (compiler/'bin', compiler/'.neo-build/bin'):
        for executable in sorted(directory.glob('neoc*')):
            if executable.is_file():
                digest.update(executable.read_bytes())
    for base in bases:
        for directory,folders,names in os.walk(base):
            folders[:]=sorted(folder for folder in folders if folder not in ('node_modules','.git','__pycache__','.neo-build','.axon-build','build','.next','.neokit'))
            for name in sorted(names):
                path=Path(directory)/name
                if path.suffix in ('.neo','.ax','.view','.toml','.lock','.py'):
                    digest.update(str(base).encode());digest.update(str(path.relative_to(base)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def native_build(source, target, *, profile='release', package_root=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    env = compiler_env()
    # Generated routing/template trees benefit from a smaller forced-inlining
    # budget. Ordinary Neo programs retain the compiler's existing default.
    env.setdefault('NEO_INLINE_BUDGET', '100')
    command = [*compiler_command(env), 'build', str(source), '-o', str(target),
               '--opt-level', '0' if profile == 'dev' else '3','--package-root',str(package_root or ROOT)]
    if profile == 'dev':
        command.append('--dev')
    cache = library_cache()
    if cache is not None:
        command += ['--library-cache', str(cache), '--library-root', str(ROOT/'src/lib.neo')]
    result = subprocess.run(command,
                            env=env, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise BuildError(result.stdout+'\n'+result.stderr)
    print(result.stdout.strip(), flush=True)


def library_cache():
    value = os.environ.get('NEOKIT_LIBRARY_CACHE')
    if value == 'off': return None
    return Path(value).expanduser().resolve() if value else Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'neokit/native'


def prepare():
    """Prepare framework/runtime code without compiling any application."""
    cache = library_cache()
    if cache is None: raise BuildError('NEOKIT_LIBRARY_CACHE=off disables library preparation')
    cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='prepare-',dir=cache) as temporary:
        folder = Path(temporary)
        source = folder/'prepare.neo'
        source.write_text('import '+literal(str(ROOT/'src/lib.neo'))+'\neffect fn main() -> unit = noop()\n')
        native_build(source,folder/'prepare')
    print('Prepared checked NeoKit libraries: '+str(cache),flush=True)


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
            raise BuildError(f'{config_path}: loader must name a Neo function')
        if config.get('mode', 'ssr') not in ('ssr', 'static'):
            raise BuildError(f'{config_path}: mode must be ssr or static')
        for flag in ('client','export','async'):
            if flag in config and not isinstance(config[flag],bool):
                raise BuildError(f'{config_path}: {flag} must be a boolean')
        async_timeout=config.get('async_timeout_ms',10000)
        if type(async_timeout)!=int or not 1<=async_timeout<=120000:raise BuildError(f'{config_path}: async_timeout_ms must be 1..120000')
        if config.get('async') and config.get('mode')=='static':raise BuildError('async pages require mode=ssr')
        ttl=config.get('cache_ttl',0)
        if type(ttl)!=int or ttl<0 or ttl>86400: raise BuildError(f'{config_path}: cache_ttl must be 0..86400 seconds')
        stale=config.get('stale_ttl',0)
        if type(stale)!=int or stale<0 or stale>86400 or stale and not ttl:raise BuildError('stale_ttl must be 0..86400 and requires cache_ttl')
        if not isinstance(config.get('cache_tag',route),str):raise BuildError(f'{config_path}: cache_tag must be text')
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
            if not matches(route,path):
                raise BuildError(f'{config_path}: path {path!r} does not match {route}')
        if config.get('mode') == 'static' and dynamic:
            raise BuildError('Dynamic routes can be snapshotted for export; static server routes must have fixed paths in v0.1')
        routes.append(dict(path=route,key=key,config=config,template=template,source=folder/'page.neo',paths=paths,dynamic=dynamic))
    if not routes:
        raise BuildError('No app/**/page.toml routes found')
    routes.sort(key=lambda r:precedence(r['path']))
    signatures = [shape(r['path']) for r in routes]
    if len(set(signatures)) != len(signatures):
        raise BuildError('Ambiguous routes differ only in parameter names')
    return routes


def css_inputs(app):
    config = tomllib.loads((app/'web.toml').read_text()).get('css', {})
    if not isinstance(config, dict):
        raise BuildError('web.toml [css] must be a table')
    inputs = config.get('tailwind', [])
    if not isinstance(inputs, list) or any(not isinstance(name, str) or
            not re.fullmatch(r'[a-zA-Z0-9_-]+\.css', name) for name in inputs):
        raise BuildError('[css] tailwind must list CSS filenames from styles/, e.g. ["site.css"]')
    if len(set(inputs)) != len(inputs):
        raise BuildError('[css] tailwind contains duplicate filenames')
    for name in inputs:
        if not (app/'styles'/name).is_file():
            raise BuildError(f'Missing Tailwind input: {app/"styles"/name}')
    return set(inputs)


def compile_css(app, source, directory):
    # Use an installed, pinned CLI, never bunx's implicit package download.
    package = next((base/'node_modules/@tailwindcss/cli/package.json'
                    for base in (app, ROOT)
                    if (base/'node_modules/@tailwindcss/cli/package.json').is_file()), None)
    if package is None:
        raise BuildError('Tailwind is enabled: run bun install in the NeoKit project, '
                         'or bun add --dev --exact tailwindcss @tailwindcss/cli in your app')
    metadata = json.loads(package.read_text())
    entry = package.parent/metadata['bin']['tailwindcss']
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='.tailwind-', dir=directory) as temporary:
        output = Path(temporary)/'output.css'
        result = subprocess.run(['bun', str(entry), '--input', str(source),
                                 '--output', str(output), '--cwd', str(app), '--minify', '--silent'],
                                cwd=app, capture_output=True, text=True)
        if result.returncode:
            raise BuildError(f'Tailwind failed for {source}:\n{result.stdout}{result.stderr}')
        content = output.read_bytes()
    print(f'Tailwind {metadata["version"]}: {source.name}, {len(content)} bytes, '
          f'{(time.perf_counter()-start)*1000:.0f}ms', flush=True)
    return content


def bundle_assets(app, out):
    directory = out/'assets'
    directory.mkdir(parents=True, exist_ok=True)
    assets = {}
    tailwind = css_inputs(app)
    entry = app/'client/index.js' if (app/'client/index.js').is_file() else ROOT/'client/index.js'
    if entry.is_file():
        subprocess.run(['bun', 'build', str(entry), '--target=browser', '--format=esm',
                        '--splitting', '--minify', '--reject-unresolved', '--entry-naming=client-[hash].js',
                        '--chunk-naming=chunk-[hash].js', '--outdir='+str(directory)], check=True)
        entries = list(directory.glob('client-*.js'))
        if len(entries) != 1:
            raise BuildError('Expected one browser client entry')
        assets['client.js'] = '/_web/'+entries[0].name
        (directory/'neokit-client.js').write_text('import '+json.dumps('./'+entries[0].name)+';\n')
    for file in sorted((app/'styles').glob('*.css')):
        content = compile_css(app, file, directory) if file.name in tailwind else file.read_bytes()
        name = file.stem+'-'+hashlib.sha256(content).hexdigest()[:12]+'.css'
        (directory/name).write_bytes(content)
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
    return 'web_route_match('+literal(route['path'])+',request.path)' if route['dynamic'] else 'str_equal(request.path,'+literal(route['path'])+')'


def route_request(route, expression='request'):
    return 'web_routed('+expression+','+literal(route['path'])+')' if route['dynamic'] else expression


def allowed_method(allowed):
    return '('+' || '.join('str_equal(request.method,'+literal(method)+')' for method in allowed)+')'

def realtime_options(config):
    options=config.get('realtime',{})
    if not isinstance(options,dict):raise BuildError('[realtime] must be a table')
    values=[]
    for name,default,minimum,maximum in [('channels',16,1,64),('history',128,1,1024),('max_event_bytes',4096,64,8192)]:
        value=options.get(name,default)
        if type(value) is not int or not minimum<=value<=maximum:raise BuildError(f'[realtime] {name} must be an integer from {minimum} to {maximum}')
        values.append(value)
    channels,history,payload=values
    if 64+channels*(128+history*(80+payload))>64*1024*1024:raise BuildError('[realtime] shared event storage exceeds 64 MiB')
    return channels,history,payload


def validate_output(app,out):
    if out == app or out in app.parents or out == ROOT or out in ROOT.parents:
        raise BuildError('Build output must not replace source directories')
    sources = [ROOT/'src', ROOT/'compiler', ROOT/'client', ROOT/'tests']
    if out != app/'.neokit/build':
        sources.append(app)
    for source in sources:
        if source == out or source in out.parents:
            raise BuildError('Build output must be outside source directories')
    if out.exists() and any(out.iterdir()) and not any((out/name).is_file() for name in ('.neokit-build', '.axon-web-build')):
        raise BuildError('Build output must be empty or an existing NeoKit build')


def build(app=DEFAULT_APP, out=None, *, target='all', profile='release'):
    if target not in ('server', 'static', 'all'):
        raise BuildError('Build target must be server, static or all')
    if profile not in ('release','dev'):
        raise BuildError('Build profile must be release or dev')
    app = Path(app).resolve()
    if not (app/'web.toml').is_file():
        raise BuildError(f'No web.toml in {app}')
    out = Path(out or ROOT/'build/studio').resolve()
    validate_output(app,out)
    out.mkdir(parents=True, exist_ok=True)
    (out/'.neokit-build').write_text('neokit v0.1\n')
    # Publish only this build's assets; old hashed entries must not leak into dispatch.
    if (out/'assets').exists():
        shutil.rmtree(out/'assets')
    generated = out/'generated'
    generated.mkdir(exist_ok=True)
    site_config=tomllib.loads((app/'web.toml').read_text())
    event_channels,event_history,event_payload=realtime_options(site_config)
    server_config=site_config.get('server',{})
    if not isinstance(server_config,dict):raise BuildError('[server] must be a table')
    for flag in ('access_log','timing'):
        if type(server_config.get(flag,False)) is not bool:raise BuildError(flag+' must be a boolean')
    grace=server_config.get('shutdown_grace_ms',30000)
    if type(grace) is not int or not 1<=grace<=120000:raise BuildError('shutdown_grace_ms must be an integer from 1 to 120000')
    hooks={name:server_config.get(name) for name in ('before','after','startup')}
    for name,handler in hooks.items():
        if handler is not None and (not isinstance(handler,str) or not NAME.fullmatch(handler)):raise BuildError(f'[server] {name} must name a Neo function')
    routes = discover(app)
    assets, files = bundle_assets(app, out)
    if profile=='dev':
        files['/_web/neokit-dev.js']=(ROOT/'client/dev.js').read_bytes()
        files['/_web/neokit-dev.css']=b'#neokit-dev-error{position:fixed;inset:1rem;z-index:2147483647;overflow:auto;padding:2rem;background:#211522;color:#fff;border:2px solid #ef7c8e;white-space:pre-wrap;font:14px/1.6 ui-monospace,monospace}'
    extra_files,extra_bindings=generated_assets(app,site_config,routes)
    assets.update(extra_bindings)
    for url,data in extra_files.items():
        if url in files:raise BuildError(f'Generated asset conflicts with public file: {url}')
        files[url]=data
    components = {p.stem:parse(p.stem,p.read_text(),p) for p in sorted((app/'components').glob('*.view'))}
    if any(not NAME.fullmatch(name) or name.startswith('Page_') or name == 'Layout' for name in components):
        raise BuildError('Invalid or reserved component name')
    layout_source=(app/'app/layout.view').read_text()
    if profile=='dev':layout_source=layout_source.replace('</head>','<link rel="stylesheet" href="/_web/neokit-dev.css"></head>').replace('</body>','<script type="module" src="/_web/neokit-dev.js"></script></body>')
    layout = parse('Layout',layout_source, app/'app/layout.view')
    if layout.props != {'title':'text','content':'html','client':'bool','mode':'text'}:
        raise BuildError('layout.view props must be title:text, content:html, client:bool, mode:text')
    layouts = {app/'app': layout}
    for path in sorted((app/'app').rglob('layout.view')):
        if path.parent==app/'app': continue
        nested=parse('Layout_'+hashlib.sha256(str(path.relative_to(app)).encode()).hexdigest()[:10],path.read_text(),path)
        if nested.props!=layout.props:
            raise BuildError(f'{path}: nested layouts use the root layout props')
        layouts[path.parent]=nested
    templates = [*components.values(),*(r['template'] for r in routes),*layouts.values()]
    code = ['import '+literal(str(ROOT/'src/lib.neo'))]
    if server_config.get('access_log',False) or server_config.get('timing',False):code.append('import "std:http_observability"')
    if any(hooks.values()):code.append('import '+literal(str(app/'app/hooks.neo')))
    error_path=app/'app/not-found.view'
    if error_path.is_file():
        error_template=parse('NotFound',error_path.read_text(),error_path)
        if error_template.props:raise BuildError('not-found.view currently takes @props()')
        templates.append(error_template)
    if any(r['config'].get('cache_ttl',0)>0 for r in routes):code.append('import '+literal(str(ROOT/'src/cache.neo')))
    for route in routes:
        code.append('import '+literal(str(route['source'])))
    async_routes={r['path']:r['config'].get('async_timeout_ms',10000) for r in routes if r['config'].get('async')}
    api_routes = []
    realtime_routes = set()
    for path in sorted((app/'app').rglob('route.toml')):
        config = tomllib.loads(path.read_text())
        if 'realtime' in config and type(config['realtime']) is not bool:raise BuildError(f'{path}: realtime must be a boolean')
        if config.get('realtime'):
            if config.get('async') or 'input' in config or config.get('methods',['GET'])!=['GET']:raise BuildError(f'{path}: realtime routes require GET, no typed action and no async admission wrapper')
            config={**config,'stream':True}
        if 'async' in config and type(config['async']) is not bool:raise BuildError(f'{path}: async must be a boolean')
        async_timeout=config.get('async_timeout_ms',10000)
        if type(async_timeout)!=int or not 1<=async_timeout<=120000:raise BuildError(f'{path}: async_timeout_ms must be 1..120000')
        if 'stream' in config and type(config['stream']) is not bool:
            raise BuildError(f'{path}: stream must be a boolean')
        url = route_path(path.parent, app/'app')
        if config.get('realtime'):realtime_routes.add(url)
        if config.get('async'):async_routes[url]=async_timeout
        if not NAME.fullmatch(config.get('handler','')):
            raise BuildError('API routes require a named Neo handler')
        code.append('import '+literal(str(path.parent/'route.neo')))
        if 'input' in config:
            if config.get('stream'):raise BuildError('Typed action adapters return buffered WebResponse values')
            code.append('import "std:http_input"')
            adapter='Action_'+hashlib.sha256(url.encode()).hexdigest()[:12]
            code.append(action_adapter(config,adapter))
            config={**config,'handler':adapter,'methods':config.get('methods',['POST'])}
        api_routes.append((url,config['handler'],methods(config,path),bool(config.get('stream',False))))
    if realtime_routes:code.append('import '+literal(str(ROOT/'src/realtime.neo')))
    all_paths = [r['path'] for r in routes]+[url for url,_,_,_ in api_routes]+list(files)
    if len(set(all_paths)) != len(all_paths):
        raise BuildError('A page/API/asset route is duplicated')
    if len({shape(path) for path in all_paths})!=len(all_paths):
        raise BuildError('Ambiguous page/API/asset routes differ only in parameter names')
    server_error=app/'app/error.view'
    if server_error.is_file():
        error=parse('ServerError',server_error.read_text(),server_error)
        if error.props!={'status':'int','message':'text'}:raise BuildError('error.view takes status:int,message:text')
        templates.append(error)
    expressions = {}
    code.append(compile_all(templates, components, assets, expressions))
    for route in routes:
        props = route['template'].props
        args = ','.join('props.'+name for name in props)
        key = route['key']
        code.append(f'effect fn body_{key}(request: WebRequest) -> WebHtml = let props={route["config"]["loader"]}(request) in view_{key}({args})')
        client = 'true' if route['config'].get('client', False) else 'false'
        # The full document is one concat: the layout's pieces with the page's
        # text_* call in its content slot, which the compiler flattens in place.
        def wrap(template, content):
            _,expression=expressions[template.name]
            values={'title':'props.title','client':client,'mode':'(if request.server then "native SSR" else "static export")'}
            expression=expression.replace('p_content.bytes',content)
            for name,value in values.items():
                expression=re.sub(rf'\bp_{name}\b',value,expression)
            return expression
        document=f'text_{key}({args})'
        ancestors=[folder for folder in route['source'].parent.parents if folder!=app/'app' and folder.is_relative_to(app/'app')]
        if route['source'].parent!=app/'app': ancestors.insert(0,route['source'].parent)
        for folder in ancestors:
            if folder not in layouts:continue
            nested=layouts[folder]
            outlet='concat('+literal('<div data-web-outlet="'+nested.name+'">')+','+document+','+literal('</div>')+')'
            document='concat('+literal('<div data-web-layout="'+nested.name+'">')+','+wrap(nested,outlet)+','+literal('</div>')+')'
        document=wrap(layout,document)
        head=metadata(site_config,route['config'])
        if head:
            document=document.replace('</head>',literal(head)[1:-1]+'</head>')
        code.append(f'effect fn render_{key}(request: WebRequest, fragment: bool) -> WebHtml = let props={route["config"]["loader"]}(request) in if fragment then view_{key}({args}) else web_html({document})')
    build_identity=cache_identity(app) if any(route['config'].get('cache_ttl',0)>0 for route in routes) else ''
    for route in routes:
        ttl=route['config'].get('cache_ttl',0)
        if not ttl:continue
        key=route['key'];tag=literal(route['config'].get('cache_tag',route['path']))
        # Content identity prevents old cached pages surviving a changed build.
        identity=hashlib.sha256((build_identity+''.join(code)).encode()).hexdigest()[:16]
        stale=route['config'].get('stale_ttl',0)
        code.append(f'effect fn cached_{key}(request:WebRequest,fragment:bool) -> WebResponse = '
            f'if !neokit_cacheable(request) then web_document(render_{key}(request,fragment)) else '
            f'let cache=neokit_cache() in let key=concat({literal(identity)},request.path,"?",request.query,if fragment then ":fragment" else ":document") in '
            f'let entry=web_cache_tagged_key(cache,key,{tag},request.stamp) in let hit=web_cache_read(cache,entry,request.stamp) in '
            f'let configured=env_get("NEOKIT_REVALIDATE_TOKEN").value in let supplied=web_request_header(request,"x-neokit-revalidate") in '
            f'let force=len(configured)>=32 && crypto_equal(configured,supplied) in '
            f'if hit.present && !force then let split=str_find(hit.value,"\\n") in let until=int_parse(str_slice(hit.value,0,split.value)) in '
            f'let stale=until.ok && until.value<=request.stamp in '
            f'let queued=if stale && len(configured)>=32 && !fragment then web_cache_enqueue(cache,entry,concat(request.path,if len(request.query)>0 then concat("?",request.query) else ""),request.stamp) else int_ok(0) in '
            f'web_with_header(web_with_header(web_reply(200,str_slice(hit.value,split.value+1,len(hit.value)),"text/html; charset=utf-8"),"Cache-Control","public, max-age={ttl}, stale-while-revalidate={stale}"),"X-NeoKit-Cache",if stale then "STALE" else "HIT") else '
            f'let response=web_document(render_{key}(request,fragment)) in '
            f'let stored=web_cache_write(cache,entry,concat(int_text(request.stamp+{ttl*1000}),"\\n",response.body),request.stamp,{(ttl+stale)*1000}) in '
            f'web_with_header(web_with_header(response,"Cache-Control","public, max-age={ttl}, stale-while-revalidate={stale}"),"X-NeoKit-Cache","MISS")')
    (generated/'common.neo').write_text('\n'.join(code)+'\n')
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
    render_code = ['import "common.neo"', 'effect fn main(index: i64) -> unit = let body = ']
    for index,(route,path) in enumerate(export_routes):
        request = 'web_static_request('+literal(path)+')'
        if route['dynamic']:
            request = 'web_routed('+request+','+literal(route['path'])+')'
        render_code.append(f'if index=={index} then render_{route["key"]}({request},false).bytes else ')
    render_code.append('"" in let written=print_out(body) in noop()')
    (generated/'render.neo').write_text('\n'.join(render_code)+'\n')
    snapshots = {}
    if export_routes or target != 'server':
        native_build(generated/'render.neo',out/'render',profile=profile,package_root=package_context(app))
        for index,(route,path) in enumerate(export_routes):
            snapshots[path] = subprocess.check_output([str(out/'render'),str(index)])
    server = ['import "common.neo"', 'effect fn web_dispatch(request: WebRequest, fragment: bool) -> WebResponse =']
    for url,data in files.items():
        ctype = 'text/javascript; charset=utf-8' if url.endswith('.js') else mimetypes.guess_type(url)[0] or 'application/octet-stream'
        response='web_reply(200,'+literal(data)+','+literal(ctype)+') with { immutable='+('true' if url.startswith('/_web/') and url!='/_web/neokit-client.js' else 'false')+' }'
        packed=compressed(data) if ctype.startswith(('text/','application/javascript','application/json','image/svg')) else None
        if packed:
            gzip_response='web_with_header(web_with_header(('+response.replace(literal(data),literal(packed))+'),"Content-Encoding","gzip"),"Vary","Accept-Encoding")'
            response='(if web_accepts_gzip(web_request_header(request,"accept-encoding")) then '+gzip_response+' else web_with_header(('+response+'),"Vary","Accept-Encoding"))'
        etag=literal('W/"'+hashlib.sha256(data).hexdigest()[:24]+'"')
        response='web_with_header(('+response+'),"ETag",'+etag+')'
        server.append('if str_equal(request.path,'+literal(url)+') then (if !'+allowed_method(['GET','HEAD'])+' then web_method_not_allowed("GET, HEAD") else if str_equal(web_request_header(request,"if-none-match"),'+etag+') then web_status(web_reply(200,"",'+literal(ctype)+'),304) else '+response+') else')
    server.append('if str_equal(request.path,"/healthz") then (if !'+allowed_method(['GET','HEAD'])+' then web_method_not_allowed("GET, HEAD") else web_json('+literal('{"ok":true}')+')) else')
    if profile=='dev':server.append('if str_equal(request.path,"/_neokit/dev") then web_json(let status=fs_read(env_get("NEOKIT_DEV_STATUS").value) in if status.ok then status.value else '+literal('{"revision":0,"error":null}')+') else')
    dispatch_routes=[]
    for route in routes:
        condition = route_condition(route)
        if route['config'].get('mode') == 'static' and target != 'static':
            if route['path'] not in snapshots:
                raise BuildError('Static server routes must have export=true')
            body = literal(snapshots[route['path']])
            response = 'web_reply(200,'+body+',"text/html; charset=utf-8")'
        else:
            response = ('cached_'+route['key']+'('+route_request(route)+',fragment)' if route['config'].get('cache_ttl',0)>0 else 'web_document(render_'+route['key']+'('+route_request(route)+',fragment))')
        dispatch_routes.append((route['path'],'if '+condition+' then (if !'+allowed_method(['GET','HEAD'])+' then web_method_not_allowed("GET, HEAD") else '+response+') else'))
    for url,handler,allowed,streaming in sorted(api_routes,key=lambda r:precedence(r[0])):
        route={'path':url,'dynamic':'[' in url}
        response=('web_reply(200,"","text/event-stream; charset=utf-8")' if url in realtime_routes else 'web_reply(200,"","text/html; charset=utf-8")') if streaming else handler+'('+route_request(route)+')'
        dispatch_routes.append((url,'if '+route_condition(route)+' then (if !'+allowed_method(allowed)+' then web_method_not_allowed('+literal(', '.join(allowed))+') else '+response+') else'))
    server.extend(code for _,code in sorted(dispatch_routes,key=lambda r:precedence(r[0])))
    server.append('web_reply(404,text_NotFound(),"text/html; charset=utf-8")' if error_path.is_file() else 'web_reply(404,"Not Found","text/plain; charset=utf-8")')
    stream_dispatch=''
    for url,handler,allowed,streaming in sorted(api_routes,key=lambda r:precedence(r[0])):
        if not streaming:continue
        route={'path':url,'dynamic':'[' in url}
        prior=[route_condition({'path':path,'dynamic':'[' in path}) for path in all_paths if precedence(path)<precedence(url)]
        blocked=' || '.join(prior+['str_equal(request.path,"/healthz")'])
        stream_dispatch+='if !head && '+route_condition(route)+' && !('+blocked+') then (if !'+allowed_method(allowed)+' then web_encode(web_method_not_allowed('+literal(', '.join(allowed))+'),false) else '+handler+'('+route_request(route)+')) else '
    guard=('let guard='+hooks['before']+'(request) in if guard.status!=0 then web_encode(guard,head) else ') if hooks['before'] else ''
    dispatch='web_dispatch(request,str_equal(web_header(raw,"x-web-fragment"),"1"))'
    if hooks['after']:dispatch=hooks['after']+'(request,'+dispatch+')'
    if server_error.is_file():
        server.append('fn web_server_error(response:WebResponse) -> WebResponse = if response.status>=500 then response with {body=text_ServerError(response.status,"The server could not complete this request."),content_type="text/html; charset=utf-8"} else response')
        dispatch='web_server_error('+dispatch+')'
    async_stream_dispatch=''
    for url,handler,allowed,streaming in sorted(api_routes,key=lambda r:precedence(r[0])):
        if not streaming or url not in async_routes:continue
        route={'path':url,'dynamic':'[' in url}
        prior=[route_condition({'path':path,'dynamic':'[' in path}) for path in all_paths if precedence(path)<precedence(url)]
        condition=route_condition(route)
        if prior:condition=condition+' && !('+' || '.join(prior)+')'
        async_stream_dispatch+='if !head && '+condition+' then (if !'+allowed_method(allowed)+' then web_encode_close(web_method_not_allowed('+literal(', '.join(allowed))+'),false) else '+handler+'('+route_request(route)+')) else '
    buffered='effect(fs,net,env,time,process) fn web_handle_buffered(raw:Arr u8 n)->Arr u8 _ = let clock=time_unix_ms() in let request=web_request(raw,clock.value) in let head=str_equal(request.method,"HEAD") in '+guard+async_stream_dispatch+'web_encode_close('+dispatch+',head)'
    async_dispatch=''
    for url,timeout in sorted(async_routes.items(),key=lambda item:precedence(item[0])):
        route={'path':url,'dynamic':'[' in url}
        prior=[route_condition({'path':path,'dynamic':'[' in path}) for path in all_paths if precedence(path)<precedence(url)]
        condition=route_condition(route)
        prior.append('str_equal(request.path,\"/healthz\")')
        if profile=='dev':prior.append('str_equal(request.path,\"/_neokit/dev\")')
        if prior:condition=condition+' && !('+' || '.join(prior)+')'
        async_dispatch+='if '+condition+' then let timer=deadline_after('+str(timeout)+') in let started=http_response_async(raw,function(web_handle_buffered),timer.value) in if started.ok then started.value else web_encode(web_reply(503,"Async capacity unavailable","text/plain; charset=utf-8"),head) else '
    if async_routes:
        server.append(buffered)
    observed=server_config.get('access_log',False) or server_config.get('timing',False)
    observed_code=''
    if observed:
        measured='web_server_timing(wire,start.value,end.value)' if server_config.get('timing',False) else 'wire'
        traced='web_trace(raw,'+measured+',start.value,end.value)' if server_config.get('access_log',False) else measured
        observed_code='effect fn observed_handle(raw:Arr u8 n) -> Arr u8 _ = let start=monotonic_ns() in let wire=web_handle(raw) in let end=monotonic_ns() in '+traced+'\n'
    server.extend([
        'effect fn web_handle(raw: Arr u8 n) -> Arr u8 _ = let clock=time_unix_ms() in let request=web_request(raw,clock.value) in let head=str_equal(request.method,"HEAD") in '+async_dispatch+guard+stream_dispatch+'web_encode('+dispatch+',head)',
        observed_code+'effect fn main(port: i64{port>0,port<65536}) -> i64 = let configured=http_server_options('+str(grace)+',true) in check configured.ok in '+(f'let events=events_configure({event_channels},{event_history},{event_payload}) in check events.ok in ' if realtime_routes else '')+('let startup='+hooks['startup']+'() in check startup.ok in ' if hooks['startup'] else '')+'serve_effect(port,'+('observed_handle' if observed else 'web_handle')+')',
    ])
    (generated/'server.neo').write_text('\n'.join(server)+'\n')
    if target != 'static':
        native_build(generated/'server.neo',out/'server',profile=profile,package_root=package_context(app))
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
    omitted = [r['path'] for r in routes if not r['config'].get('export',True)]+[url for url,_,_,_ in api_routes]
    revalidator=None
    if target!='static' and any(r['config'].get('stale_ttl',0)>0 for r in routes):
        revalidator=out/'revalidate';native_build(ROOT/'src/revalidate.neo',revalidator,package_root=package_context(app))
    migration=build_migration(app,out) if target!='static' and (app/'app/migrate.neo').is_file() else None
    manifest = {'app':str(app),'routes':[{'path':r['path'],'mode':r['config'].get('mode','ssr'),'async':r['config'].get('async',False),'client':r['config'].get('client',False),'export':r['config'].get('export',True),'paths':r['paths']} for r in routes],
                'assets':assets,'files':{url:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for url,data in files.items()},'exported_paths':list(snapshots),'available_export_paths':available_export_paths,'omitted_server_routes':omitted,
                'build_target':target,'profile':profile,'migration_binary':str(migration) if migration else None,'revalidator_binary':str(revalidator) if revalidator else None,'server_bytes':(out/'server').stat().st_size if target != 'static' else None,'browser_js_bytes':sum(len(data) for url,data in files.items() if url.endswith('.js'))}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return out, manifest


def build_migration(app,out):
    app=Path(app).resolve();out=Path(out).resolve()
    validate_output(app,out)
    source=app/'app/migrate.neo'
    if not source.is_file():raise BuildError('Application has no app/migrate.neo migration entry')
    config=tomllib.loads((app/'web.toml').read_text()).get('server',{})
    handler=config.get('migrate','migrate')
    if not isinstance(handler,str) or not NAME.fullmatch(handler):raise BuildError('[server] migrate must name a Neo function returning IntResult')
    (out/'generated').mkdir(parents=True,exist_ok=True)
    (out/'.neokit-build').write_text('neokit v0.1\n')
    generated=out/'generated/migrate.neo'
    generated.write_text('import "std:process"\nimport '+literal(str(source))+'\neffect fn main()->unit = let result='+handler+'() in if !result.ok then let error=print_error(result.error.message) in process_exit(1) else let done=print_out("Migrations applied\\n") in noop()\n')
    native_build(generated,out/'migrate',package_root=package_context(app))
    return out/'migrate'
