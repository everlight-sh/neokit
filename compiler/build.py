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

from .templates import BuildError, NAME, compile_all, literal, parse, substitute
from .routes import route_path, matches, precedence, shape, methods, canonical_path
from .assets import (metadata, metadata_props, dynamic_metadata, generated_assets, compressed,
                     brotli_compressed, compressible)
from .actions import action_adapter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT/'examples/studio'
LAYOUT_PROPS = {'title':'text','content':'html','client':'bool','mode':'text'}
HTML = 'text/html; charset=utf-8'
IMMUTABLE = 'public, max-age=31536000, immutable'
REVALIDATE = 'public, no-cache'
CONTENT_TYPES = {'.js':'text/javascript; charset=utf-8', '.mjs':'text/javascript; charset=utf-8',
                 '.css':'text/css; charset=utf-8', '.html':HTML, '.txt':'text/plain; charset=utf-8',
                 '.json':'application/json; charset=utf-8', '.map':'application/json; charset=utf-8',
                 '.webmanifest':'application/manifest+json', '.xml':'application/xml', '.svg':'image/svg+xml',
                 '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.gif':'image/gif',
                 '.webp':'image/webp', '.avif':'image/avif', '.ico':'image/x-icon', '.woff2':'font/woff2',
                 '.woff':'font/woff', '.wasm':'application/wasm', '.pdf':'application/pdf'}
REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def compiler_env():
    def native(root):
        return (root/'bin/neoc').is_file() or (root/'.neo-build/bin/neoc').is_file()

    launcher = shutil.which('neo')
    candidates = [ROOT.parent/'neo']
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
    return next((root for root in (path,*path.parents) if any((root/name).is_file() for name in ('neo.toml',))),ROOT)


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
            folders[:]=sorted(folder for folder in folders if folder not in ('node_modules','.git','__pycache__','.neo-build','build','.next','.neokit'))
            for name in sorted(names):
                path=Path(directory)/name
                if path.suffix in ('.neo','.ax','.view','.toml','.lock','.py'):
                    digest.update(str(base).encode());digest.update(str(path.relative_to(base)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def native_build(source, target, *, profile='release', package_root=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    env = compiler_env()
    # Generated routing/template trees benefit from a smaller forced-inlining
    # budget. Ordinary Neo programs retain the compiler's existing default.
    env.setdefault('NEO_INLINE_BUDGET', '100')
    # Build beside the target, then rename: a running server keeps its inode
    # and a failed build leaves the previous executable untouched.
    staged = target.with_name(target.name+'.next')
    command = [*compiler_command(env), 'build', str(source), '-o', str(staged),
               '--opt-level', '0' if profile == 'dev' else '3','--package-root',str(package_root or ROOT)]
    if profile == 'dev':
        command.append('--dev')
    cache = library_cache()
    if cache is not None:
        command += ['--library-cache', str(cache), '--library-root', str(ROOT/'src/lib.neo')]
    result = subprocess.run(command,
                            env=env, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        staged.unlink(missing_ok=True)
        raise BuildError(result.stdout+'\n'+result.stderr)
    os.replace(staged, target)
    if result.stdout.strip():
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


def optional_name(config, key, path):
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not NAME.fullmatch(value)):
        raise BuildError(f'{path}: {key} must name a Neo function')
    return value


def discover(app):
    routes = []
    for config_path in sorted((app/'app').rglob('page.toml')):
        config = tomllib.loads(config_path.read_text())
        folder = config_path.parent
        route = route_path(folder, app/'app')
        if route in ('/_web','/_neokit') or route.startswith(('/_web/','/_neokit/')):
            raise BuildError('The /_web/ and /_neokit/ namespaces are reserved')
        key = 'Page_'+hashlib.sha256(route.encode()).hexdigest()[:10]
        if not NAME.fullmatch(config.get('loader', '')):
            raise BuildError(f'{config_path}: loader must name a Neo function')
        mode = config.get('mode', 'ssr')
        if mode not in ('ssr', 'static'):
            raise BuildError(f'{config_path}: mode must be ssr or static')
        for flag in ('client','export','async'):
            if flag in config and not isinstance(config[flag],bool):
                raise BuildError(f'{config_path}: {flag} must be a boolean')
        async_timeout=config.get('async_timeout_ms',10000)
        if type(async_timeout)!=int or not 1<=async_timeout<=120000:raise BuildError(f'{config_path}: async_timeout_ms must be 1..120000')
        if config.get('async') and mode=='static':raise BuildError('async pages require mode=ssr')
        ttl=config.get('cache_ttl',0)
        if type(ttl)!=int or ttl<0 or ttl>86400: raise BuildError(f'{config_path}: cache_ttl must be 0..86400 seconds')
        stale=config.get('stale_ttl',0)
        if type(stale)!=int or stale<0 or stale>86400 or stale and not ttl:raise BuildError('stale_ttl must be 0..86400 and requires cache_ttl')
        if not isinstance(config.get('cache_tag',route),str):raise BuildError(f'{config_path}: cache_tag must be text')
        query=config.get('cache_query')
        if query is not None:
            if not ttl:raise BuildError(f'{config_path}: cache_query requires cache_ttl')
            if not isinstance(query,list) or len(query)>16 or len(set(query))!=len(query) or any(not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',name) for name in query):
                raise BuildError(f'{config_path}: cache_query must list up to 16 distinct query parameter names')
        control=config.get('cache_control')
        if control is not None:
            if not isinstance(control,str) or not re.fullmatch(r'[\x20-\x7e]{1,200}',control):raise BuildError(f'{config_path}: cache_control must be one header value')
            if ttl or mode=='static':raise BuildError(f'{config_path}: cache_control is for uncached SSR pages; cache_ttl and static pages set their own')
        guard=optional_name(config,'guard',config_path)
        if guard and mode=='static':raise BuildError(f'{config_path}: static pages are prerendered and cannot run a guard')
        if guard:
            # Exporting would publish a guarded page without running its guard.
            if config.get('export') is True:raise BuildError(f'{config_path}: guarded pages cannot be exported')
            config={**config,'export':False}
        paths_fn=optional_name(config,'paths_fn',config_path)
        template = parse(key, (folder/'page.view').read_text(), folder/'page.view')
        if template.props.get('title') != 'text':
            raise BuildError(f'{config_path}: pages require a title: text prop')
        dynamic = '[' in route
        if paths_fn and not dynamic:raise BuildError(f'{config_path}: paths_fn is for dynamic routes')
        paths = config.get('paths', [] if dynamic else [route])
        if not isinstance(paths,list) or any(not isinstance(path,str) for path in paths):
            raise BuildError(f'{config_path}: paths must be a list of URL strings')
        for path in paths:
            if not canonical_path(path):
                raise BuildError(f'{config_path}: export paths must be canonical URL paths')
            if not matches(route,path):
                raise BuildError(f'{config_path}: path {path!r} does not match {route}')
        if mode == 'static' and dynamic and not paths and not paths_fn:
            raise BuildError(f'{config_path}: static dynamic routes need paths or paths_fn')
        metadata_props(config)
        routes.append(dict(path=route,key=key,config=config,template=template,source=folder/'page.neo',paths=list(paths),dynamic=dynamic,guard=guard,paths_fn=paths_fn))
    if not routes:
        raise BuildError('No app/**/page.toml routes found')
    routes.sort(key=lambda r:precedence(r['path']))
    signatures = [shape(r['path']) for r in routes]
    if len(set(signatures)) != len(signatures):
        raise BuildError('Two pages resolve to the same route (check (group) folders and parameter names)')
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


def client_entry(app):
    folder = app/'client'
    return next((folder/name for name in ('index.ts','index.tsx','index.js','index.jsx') if (folder/name).is_file()), ROOT/'client/index.js')


def bundle_assets(app, out, profile='release'):
    """Bundle browser modules and CSS into out/assets, swapped in whole."""
    dev = profile == 'dev'
    staging = out/'.assets-next'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    assets = {}
    tailwind = css_inputs(app)
    entry = client_entry(app)
    if entry.is_file():
        # Development keeps stable entry/stylesheet names and source maps, so
        # browser-only edits do not change the compiled server.
        command = ['bun', 'build', str(entry), '--target=browser', '--format=esm', '--splitting',
                   '--reject-unresolved', '--entry-naming='+('client.js' if dev else 'client-[hash].js'),
                   '--chunk-naming=chunk-[hash].js', '--outdir='+str(staging),
                   '--sourcemap=linked' if dev else '--minify']
        subprocess.run(command, check=True)
        entries = list(staging.glob('client.js' if dev else 'client-*.js'))
        if len(entries) != 1:
            raise BuildError('Expected one browser client entry')
        assets['client.js'] = '/_web/'+entries[0].name
        (staging/'neokit-client.js').write_text('import '+json.dumps('./'+entries[0].name)+';\n')
    for file in sorted((app/'styles').glob('*.css')):
        content = compile_css(app, file, staging) if file.name in tailwind else file.read_bytes()
        name = file.name if dev else file.stem+'-'+hashlib.sha256(content).hexdigest()[:12]+'.css'
        (staging/name).write_bytes(content)
        assets[file.name] = '/_web/'+name
    if dev:
        (staging/'neokit-dev.js').write_bytes((ROOT/'client/dev.js').read_bytes())
        (staging/'neokit-dev.css').write_text('#neokit-dev-error{position:fixed;inset:1rem;z-index:2147483647;overflow:auto;padding:2rem;background:#211522;color:#fff;border:2px solid #ef7c8e;white-space:pre-wrap;font:14px/1.6 ui-monospace,monospace}')
    directory = out/'assets'
    previous = out/'.assets-previous'
    if previous.exists():
        shutil.rmtree(previous)
    if directory.exists():
        os.replace(directory, previous)
    os.replace(staging, directory)
    if previous.exists():
        shutil.rmtree(previous)
    files = {'/_web/'+p.relative_to(directory).as_posix():p.read_bytes() for p in sorted(directory.rglob('*')) if p.is_file()}
    public = app/'public'
    for file in sorted(public.rglob('*')) if public.exists() else []:
        if file.is_symlink():
            raise BuildError('Public symlinks are not supported')
        if file.is_file():
            url = '/'+file.relative_to(public).as_posix()
            if url.startswith('/_web/') or url.startswith('/_neokit/'):
                raise BuildError('The /_web/ and /_neokit/ namespaces are reserved')
            files[url] = file.read_bytes()
    return assets, files


def refresh_assets(app, out):
    """Development: rebundle browser modules and CSS without recompiling the
    server, which reads out/assets from disk on each request."""
    app = Path(app).resolve(); out = Path(out).resolve()
    validate_output(app, out)
    bundle_assets(app, out, 'dev')
    site_config = tomllib.loads((app/'web.toml').read_text())
    extra, _ = generated_assets(app, site_config, discover(app), out/'.image-cache')
    for url, data in extra.items():
        if url.startswith('/_web/'):
            destination = out/'assets'/url[len('/_web/'):]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def public_urls(app):
    public = app/'public'
    return {'/'+p.relative_to(public).as_posix() for p in public.rglob('*') if p.is_file()} if public.exists() else set()


def content_type(url):
    suffix = Path(url).suffix.lower()
    if suffix in CONTENT_TYPES:
        return CONTENT_TYPES[suffix]
    guessed = mimetypes.guess_type(url)[0] or 'application/octet-stream'
    return guessed+'; charset=utf-8' if guessed.startswith('text/') else guessed


def route_condition(route):
    return 'web_route_match('+literal(route['path'])+',request.path)' if route['dynamic'] else 'str_equal(request.path,'+literal(route['path'])+')'


def route_request(route, expression='request'):
    return 'web_routed('+expression+','+literal(route['path'])+')' if route['dynamic'] else expression


def allowed_method(allowed):
    return '('+' || '.join('str_equal(request.method,'+literal(method)+')' for method in allowed)+')'


def header(response, name, value):
    return 'web_with_header('+response+','+literal(name)+','+value+')'


def embedded_response(data, ctype, control):
    """GET/HEAD response for bytes embedded at build time: negotiated
    gzip/Brotli variants, an ETag, and 304 replies that keep cache headers."""
    etag = literal('W/"'+hashlib.sha256(data).hexdigest()[:24]+'"')
    packed = compressed(data) if compressible(ctype) else None
    smallest = brotli_compressed(data, packed) if packed else None
    def reply(body, encoding=None, status=200):
        response = 'web_reply(200,'+literal(body)+','+literal(ctype)+')'
        if status == 304:
            response = 'web_status('+response+',304)'
        response = header(response, 'Cache-Control', literal(control))
        if encoding:
            response = header(response, 'Content-Encoding', literal(encoding))
        if packed:
            response = header(response, 'Vary', '"Accept-Encoding"')
        return header(response, 'ETag', etag)
    response = reply(data)
    if packed:
        response = '(if neokit_accepts(accepted,"gzip") then '+reply(packed,'gzip')+' else '+response+')'
    if smallest:
        response = '(if neokit_accepts(accepted,"br") then '+reply(smallest,'br')+' else '+response+')'
    if packed:
        response = 'let accepted=web_request_header(request,"accept-encoding") in '+response
    return ('(if !'+allowed_method(['GET','HEAD'])+' then web_method_not_allowed("GET, HEAD") '
            'else if neokit_not_modified(request,'+etag+') then '+reply(b'',status=304)+' else '+response+')')


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


def redirect_rules(config):
    rules = config.get('redirects', [])
    if not isinstance(rules, list):
        raise BuildError('[[redirects]] must be an array of tables')
    checked = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule)-{'from','to','status','query'}:
            raise BuildError('Each [[redirects]] entry takes from, to, status and query')
        source, target = rule.get('from'), rule.get('to')
        status = rule.get('status', 308)
        query = rule.get('query', True)
        if not canonical_path(source) or '[' in source:
            raise BuildError(f'Redirect from must be a canonical literal path: {source!r}')
        absolute = isinstance(target, str) and re.fullmatch(r'https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[\x21-\x7e]*)?', target)
        relative = isinstance(target, str) and re.fullmatch(r'/(?!/)[\x21-\x7e]*', target) and '\\' not in target
        if not (absolute or relative) or '"' in target:
            raise BuildError(f'Redirect to must be a path or an http(s) URL without spaces: {target!r}')
        if status not in REDIRECT_STATUSES or not isinstance(query, bool):
            raise BuildError(f'Redirect status must be one of {REDIRECT_STATUSES}; query must be a boolean')
        checked.append(dict(source=source, target=target, status=status, query=query, absolute=bool(absolute)))
    if len({rule['source'] for rule in checked}) != len(checked):
        raise BuildError('Duplicate redirect source')
    return checked


def redirect_response(rule):
    location = literal(rule['target'])
    if rule['query']:
        joiner = '&' if '?' in rule['target'] else '?'
        location = 'concat('+location+',if len(request.query)>0 then concat('+literal(joiner)+',request.query) else "")'
    if rule['absolute']:
        return header('web_status(web_reply(200,"","text/plain; charset=utf-8"),'+str(rule['status'])+')', 'Location', location)
    return 'web_redirect('+location+','+str(rule['status'])+')'


def cors_options(config, path, streaming):
    origins = config.get('cors')
    if origins is None:
        return None
    if streaming:
        raise BuildError(f'{path}: cors is supported on buffered API routes')
    if origins == '*':
        origins = ['*']
    if not isinstance(origins, list) or not origins or len(origins) > 32 or ('*' in origins and len(origins) > 1) or any(
            not isinstance(origin, str) or origin != '*' and not re.fullmatch(r'https?://[A-Za-z0-9.-]+(?::\d{1,5})?', origin) for origin in origins):
        raise BuildError(f'{path}: cors must be "*" or a list of origins like "https://app.example"')
    headers = config.get('cors_headers', ['content-type'])
    if not isinstance(headers, list) or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', name) for name in headers):
        raise BuildError(f'{path}: cors_headers must list request header names')
    credentials = config.get('cors_credentials', False)
    if not isinstance(credentials, bool) or credentials and origins == ['*']:
        raise BuildError(f'{path}: cors_credentials must be a boolean and cannot be used with "*"')
    if 'input' in config and config.get('csrf', True):
        raise BuildError(f'{path}: cross-origin typed actions need csrf=false and their own authentication')
    return dict(origins=origins, headers=headers, credentials=credentials)


def cors_function(name, call, allowed, cors):
    """A wrapper answering preflight and adding CORS headers to one API route."""
    wildcard = cors['origins'] == ['*']
    origin_value = '"*"' if wildcard else 'origin'
    check = 'true' if wildcard else '('+' || '.join('str_equal(origin,'+literal(origin)+')' for origin in cors['origins'])+')'
    def granted(response):
        response = header(response, 'Access-Control-Allow-Origin', origin_value)
        if cors['credentials']:
            response = header(response, 'Access-Control-Allow-Credentials', '"true"')
        return header(response, 'Vary', '"Origin"')
    preflight = header(header(header(granted('web_status(web_reply(200,"","text/plain; charset=utf-8"),204)'),
        'Access-Control-Allow-Methods', literal(', '.join(m for m in allowed if m != 'OPTIONS'))),
        'Access-Control-Allow-Headers', literal(', '.join(cors['headers']))), 'Access-Control-Max-Age', '"600"')
    refused = header('web_status(web_reply(200,"","text/plain; charset=utf-8"),204)', 'Vary', '"Origin"')
    return (f'effect fn {name}(request:WebRequest) -> WebResponse = let origin=web_request_header(request,"origin") in '
            f'let allowed={check} in if str_equal(request.method,"OPTIONS") then (if allowed then {preflight} else {refused}) else '
            f'let response={call} in if allowed && len(origin)>0 then {granted("response")} else {header("response","Vary",chr(34)+"Origin"+chr(34))}')


def validate_output(app,out):
    if out == app or out in app.parents or out == ROOT or out in ROOT.parents:
        raise BuildError('Build output must not replace source directories')
    sources = [ROOT/'src', ROOT/'compiler', ROOT/'client', ROOT/'tests']
    if out != app/'.neokit/build':
        sources.append(app)
    for source in sources:
        if source == out or source in out.parents:
            raise BuildError('Build output must be outside source directories')
    if out.exists() and any(out.iterdir()) and not any((out/name).is_file() for name in ('.neokit-build',)):
        raise BuildError('Build output must be empty or an existing NeoKit build')


def resolve_paths(routes, out, profile, package_root):
    """Run each dynamic route's paths_fn once at build time to list its pages."""
    listed = [route for route in routes if route['paths_fn']]
    if not listed:
        return
    code = ['import '+literal(str(ROOT/'src/lib.neo'))]
    code += ['import '+literal(str(source)) for source in dict.fromkeys(route['source'] for route in listed)]
    code.append('fn neokit_lines(paths:Arr Arr u8 _ n) -> Arr u8 _ = str_concat_parts(arr(i in 0..n) check i>=0 && i<n in concat(paths[i],"\\n"))')
    code.append('effect fn main() -> unit = '+''.join(f'let w{i}=print_out(concat("#\\n",neokit_lines({route["paths_fn"]}()))) in ' for i,route in enumerate(listed))+'noop()')
    source = out/'generated/paths.neo'
    source.write_text('\n'.join(code)+'\n')
    native_build(source, out/'paths', profile=profile, package_root=package_root)
    result = subprocess.run([str(out/'paths')], capture_output=True)
    if result.returncode:
        raise BuildError('paths_fn failed:\n'+result.stderr.decode(errors='replace'))
    groups = result.stdout.decode().split('#\n')[1:]
    if len(groups) != len(listed):
        raise BuildError('paths_fn produced unexpected output')
    for route, group in zip(listed, groups):
        seen = set(route['paths'])
        for path in group.splitlines():
            if not canonical_path(path) or not matches(route['path'], path):
                raise BuildError(f'{route["source"].parent/"page.toml"}: paths_fn returned {path!r}, which does not match {route["path"]}')
            if path not in seen:
                seen.add(path)
                route['paths'].append(path)
        if len(route['paths']) > 100000:
            raise BuildError(f'{route["path"]}: more than 100,000 static paths')


def read_frames(output, count):
    frames = []
    at = 0
    while at < len(output):
        newline = output.index(b'\n', at)
        size = int(output[at:newline])
        frames.append(output[newline+1:newline+1+size])
        at = newline+1+size
    if len(frames) != count:
        raise BuildError(f'Static renderer produced {len(frames)} pages; expected {count}')
    return frames


def snapshot_file(directory, path):
    relative = path.lstrip('/')
    # /feed.xml or /.well-known/security.txt export as files, other pages as folders.
    if '.' in relative.rsplit('/', 1)[-1]:
        return directory/relative
    return directory/relative/'index.html'


def build(app=DEFAULT_APP, out=None, *, target='all', profile='release'):
    if target not in ('server', 'static', 'all'):
        raise BuildError('Build target must be server, static or all')
    if profile not in ('release','dev'):
        raise BuildError('Build profile must be release or dev')
    dev = profile == 'dev'
    app = Path(app).resolve()
    if not (app/'web.toml').is_file():
        raise BuildError(f'No web.toml in {app}')
    out = Path(out or ROOT/'build/studio').resolve()
    validate_output(app,out)
    out.mkdir(parents=True, exist_ok=True)
    (out/'.neokit-build').write_text('neokit v0.1\n')
    generated = out/'generated'
    generated.mkdir(exist_ok=True)
    package_root = package_context(app)
    site_config=tomllib.loads((app/'web.toml').read_text())
    event_channels,event_history,event_payload=realtime_options(site_config)
    server_config=site_config.get('server',{})
    if not isinstance(server_config,dict):raise BuildError('[server] must be a table')
    for flag in ('access_log','timing'):
        if type(server_config.get(flag,False)) is not bool:raise BuildError(flag+' must be a boolean')
    grace=server_config.get('shutdown_grace_ms',30000)
    if type(grace) is not int or not 1<=grace<=120000:raise BuildError('shutdown_grace_ms must be an integer from 1 to 120000')
    if dev:
        # The development watcher restarts the server on every rebuild.
        grace=min(grace,1000)
    security=site_config.get('security',{})
    if not isinstance(security,dict) or set(security)-{'csp'}:raise BuildError('[security] supports only csp')
    csp=security.get('csp')
    if csp is not None and (not isinstance(csp,str) or not 0<len(csp)<=4096 or any(not 32<=ord(c)<127 for c in csp)):
        raise BuildError('[security] csp must be a Content-Security-Policy value of up to 4096 printable ASCII characters')
    hooks={name:server_config.get(name) for name in ('before','after','startup')}
    for name,handler in hooks.items():
        if handler is not None and (not isinstance(handler,str) or not NAME.fullmatch(handler)):raise BuildError(f'[server] {name} must name a Neo function')
    redirects = redirect_rules(site_config)
    routes = discover(app)
    resolve_paths(routes, out, profile, package_root)
    # Publish only this build's assets; old hashed entries must not leak into dispatch.
    assets, files = bundle_assets(app, out, profile)
    public = public_urls(app)
    extra_files,extra_bindings=generated_assets(app,site_config,routes,out/'.image-cache')
    assets.update(extra_bindings)
    for url,data in extra_files.items():
        if url in files:raise BuildError(f'Generated asset conflicts with public file: {url}')
        files[url]=data
        if dev and url.startswith('/_web/'):
            destination=out/'assets'/url[len('/_web/'):]
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(data)
    components = {p.stem:parse(p.stem,p.read_text(),p) for p in sorted((app/'components').glob('*.view'))}
    if any(not NAME.fullmatch(name) or name.startswith('Page_') or name == 'Layout' for name in components):
        raise BuildError('Invalid or reserved component name')
    layout_source=(app/'app/layout.view').read_text()
    if dev:
        before,_,after=layout_source.rpartition('</body>')
        layout_source=(before+'<script type="module" src="/_web/neokit-dev.js"></script></body>'+after) if before else layout_source
        layout_source=layout_source.replace('</head>','<link rel="stylesheet" href="/_web/neokit-dev.css"></head>',1)
    layout = parse('Layout',layout_source, app/'app/layout.view')
    if layout.props not in (LAYOUT_PROPS, {**LAYOUT_PROPS,'head':'html'}):
        raise BuildError('layout.view props must be title:text, content:html, client:bool, mode:text and optionally head:html')
    layouts = {app/'app': layout}
    for path in sorted((app/'app').rglob('layout.view')):
        if path.parent==app/'app': continue
        nested=parse('Layout_'+hashlib.sha256(str(path.relative_to(app)).encode()).hexdigest()[:10],path.read_text(),path)
        if nested.props!=layout.props:
            raise BuildError(f'{path}: nested layouts use the root layout props')
        layouts[path.parent]=nested
    templates = [*components.values(),*(r['template'] for r in routes),*layouts.values()]
    code = ['import '+literal(str(ROOT/'src/lib.neo')), 'import '+literal(str(ROOT/'src/assets.neo'))]
    if dev:code.append('import '+literal(str(ROOT/'src/dev.neo')))
    if csp is not None:code.append('import '+literal(str(ROOT/'src/security.neo')))
    if server_config.get('access_log',False) or server_config.get('timing',False):code.append('import "std:http_observability"')
    if any(hooks.values()):code.append('import '+literal(str(app/'app/hooks.neo')))
    error_path=app/'app/not-found.view'
    if error_path.is_file():
        error_template=parse('NotFound',error_path.read_text(),error_path)
        if error_template.props:raise BuildError('not-found.view currently takes @props()')
        templates.append(error_template)
    if any(r['config'].get('cache_ttl',0)>0 for r in routes):code.append('import '+literal(str(ROOT/'src/cache.neo')))
    for source in dict.fromkeys(route['source'] for route in routes):
        code.append('import '+literal(str(source)))
    async_routes={r['path']:r['config'].get('async_timeout_ms',10000) for r in routes if r['config'].get('async')}
    api_routes = []
    realtime_routes = set()
    cors_wrappers = []
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
        if url in ('/_web','/_neokit') or url.startswith(('/_web/','/_neokit/')):
            raise BuildError('The /_web/ and /_neokit/ namespaces are reserved')
        if config.get('realtime'):realtime_routes.add(url)
        if config.get('async'):async_routes[url]=async_timeout
        if not NAME.fullmatch(config.get('handler','')):
            raise BuildError('API routes require a named Neo handler')
        streaming = bool(config.get('stream',False))
        cors = cors_options(config, path, streaming)
        code.append('import '+literal(str(path.parent/'route.neo')))
        if 'input' in config:
            if streaming:raise BuildError('Typed action adapters return buffered WebResponse values')
            code.append('import "std:http_input"')
            adapter='Action_'+hashlib.sha256(url.encode()).hexdigest()[:12]
            code.append(action_adapter(config,adapter))
            config={**config,'handler':adapter,'methods':config.get('methods',['POST'])}
        allowed = methods(config,path)
        if cors:
            if 'OPTIONS' in allowed:raise BuildError(f'{path}: cors answers OPTIONS itself; remove it from methods')
            wrapper='Cors_'+hashlib.sha256(url.encode()).hexdigest()[:12]
            cors_wrappers.append(cors_function(wrapper, config['handler']+'(request)', allowed, cors))
            config={**config,'handler':wrapper}
            allowed=[*allowed,'OPTIONS']
        api_routes.append((url,config['handler'],allowed,streaming))
    if realtime_routes:code.append('import '+literal(str(ROOT/'src/realtime.neo')))
    redirect_sources = [rule['source'] for rule in redirects]
    all_paths = [r['path'] for r in routes]+[url for url,_,_,_ in api_routes]+list(files)+redirect_sources
    if len(set(all_paths)) != len(all_paths):
        raise BuildError('A page/API/asset/redirect route is duplicated')
    if len({shape(path) for path in all_paths})!=len(all_paths):
        raise BuildError('Ambiguous page/API/asset routes differ only in parameter names')
    server_error=app/'app/error.view'
    if server_error.is_file():
        error=parse('ServerError',server_error.read_text(),server_error)
        if error.props!={'status':'int','message':'text'}:raise BuildError('error.view takes status:int,message:text')
        templates.append(error)
    expressions = {}
    jsonld = False
    code.append(compile_all(templates, components, assets, expressions))
    code.extend(cors_wrappers)
    not_found_body = 'text_NotFound()' if error_path.is_file() else '"Not Found"'
    not_found_type = HTML if error_path.is_file() else 'text/plain; charset=utf-8'
    code.append(f'fn neokit_not_found() -> WebResponse = web_reply(404,{not_found_body},{literal(not_found_type)})')
    # A guard or handler returning an empty 404 gets the application's not-found page.
    code.append('fn neokit_page_response(response:WebResponse) -> WebResponse = if response.status==404 && len(response.body)==0 then let page=neokit_not_found() in page with { headers=response.headers } else response')
    for route in routes:
        props = route['template'].props
        args = ','.join('props.'+name for name in props)
        key = route['key']
        client = 'true' if route['config'].get('client', False) else 'false'
        # The full document is one concat: the layout's pieces with the page's
        # text_* call in its content slot, which the compiler flattens in place.
        def wrap(template, content, head):
            _,expression=expressions[template.name]
            mapping={'p_content.bytes':content,'p_content':'web_html('+content+')','p_title':'props.title',
                     'p_client':client,'p_mode':'(if request.server then "native SSR" else "static export")'}
            if 'head' in template.props:
                mapping['p_head.bytes']=head
                mapping['p_head']='web_html('+head+')'
            return substitute(expression,mapping)
        document=f'text_{key}({args})'
        ancestors=[folder for folder in route['source'].parent.parents if folder!=app/'app' and folder.is_relative_to(app/'app')]
        if route['source'].parent!=app/'app': ancestors.insert(0,route['source'].parent)
        for folder in ancestors:
            if folder not in layouts:continue
            nested=layouts[folder]
            outlet='concat('+literal('<div data-web-outlet="'+nested.name+'">')+','+document+','+literal('</div>')+')'
            document='concat('+literal('<div data-web-layout="'+nested.name+'">')+','+wrap(nested,outlet,'""')+','+literal('</div>')+')'
        dynamic_keys=set(metadata_props(route['config']))
        static_head=metadata(site_config,route['config'],skip=dynamic_keys)
        dynamic_head,uses_jsonld=dynamic_metadata(site_config,route['config'],props)
        jsonld=jsonld or uses_jsonld
        head_parts=([literal(static_head)] if static_head else [])+dynamic_head
        head='concat('+','.join(head_parts)+')' if len(head_parts)>1 else head_parts[0] if head_parts else '""'
        if 'head' in layout.props:
            document=wrap(layout,document,head)
        else:
            if dynamic_head:
                raise BuildError(f'{route["path"]}: [metadata.props] needs head:html in layout.view, rendered in <head> as {{{{ head }}}}')
            document=wrap(layout,document,'""')
            if static_head:
                if '</head>' not in document:raise BuildError('layout.view needs </head> for [site]/[metadata] tags, or a head:html prop')
                document=document.replace('</head>',literal(static_head)[1:-1]+'</head>',1)
        code.append(f'effect fn render_{key}(request: WebRequest, fragment: bool) -> WebHtml = let props={route["config"]["loader"]}(request) in if fragment then view_{key}({args}) else web_html({document})')
    if jsonld:code.insert(1,'import '+literal(str(ROOT/'src/head.neo')))
    build_identity=cache_identity(app) if any(route['config'].get('cache_ttl',0)>0 for route in routes) else ''
    for route in routes:
        ttl=route['config'].get('cache_ttl',0)
        if not ttl:continue
        key=route['key'];tag=literal(route['config'].get('cache_tag',route['path']))
        # Content identity prevents old cached pages surviving a changed build.
        identity=hashlib.sha256((build_identity+''.join(code)).encode()).hexdigest()[:16]
        stale=route['config'].get('stale_ttl',0)
        control=literal(f'public, max-age={ttl}'+(f', stale-while-revalidate={stale}' if stale else ''))
        query=route['config'].get('cache_query')
        if query is None:
            # Without declared parameters, any query string bypasses the shared
            # cache so arbitrary URLs cannot fill the cache directory.
            bypass=' || len(request.query)>0'
            lets=''
            query_key='""'
        else:
            bypass=''
            lets=''.join(f'let q{i}=web_query_get(request.query,{literal(name)}) in ' for i,name in enumerate(query))
            pieces=[f'if q{i}.present then concat("1:",int_text(len(q{i}.value)),":",q{i}.value) else "0"' for i in range(len(query))]
            query_key='concat("?",'+',";",'.join('('+piece+')' for piece in pieces)+')'
        code.append(f'effect fn cached_{key}(request:WebRequest,fragment:bool) -> WebResponse = '
            f'if !neokit_cacheable(request){bypass} then web_document(render_{key}(request,fragment)) else '
            f'{lets}let cache=neokit_cache() in let key=concat({literal(identity)},request.path,{query_key},if fragment then ":fragment" else ":document") in '
            f'let entry=web_cache_tagged_key(cache,key,{tag},request.stamp) in let hit=web_cache_read(cache,entry,request.stamp) in '
            f'let configured=env_get("NEOKIT_REVALIDATE_TOKEN").value in let supplied=web_request_header(request,"x-neokit-revalidate") in '
            f'let force=len(configured)>=32 && crypto_equal(configured,supplied) in '
            f'if hit.present && !force then let split=str_find(hit.value,"\\n") in let until=int_parse(str_slice(hit.value,0,split.value)) in '
            f'let stale=until.ok && until.value<=request.stamp in '
            f'let queued=if stale && len(configured)>=32 && !fragment then web_cache_enqueue(cache,entry,concat(request.path,if len(request.query)>0 then concat("?",request.query) else ""),request.stamp) else int_ok(0) in '
            f'neokit_cached(web_reply(200,str_slice(hit.value,split.value+1,len(hit.value)),"text/html; charset=utf-8"),{control},if stale then "STALE" else "HIT") else '
            f'let response=web_document(render_{key}(request,fragment)) in '
            f'let stored=web_cache_write(cache,entry,concat(int_text(request.stamp+{ttl*1000}),"\\n",response.body),request.stamp,{(ttl+stale)*1000}) in '
            f'neokit_cached(response,{control},"MISS")')
    for route in routes:
        if route['config'].get('mode')=='static':continue
        key=route['key']
        if route['config'].get('cache_ttl',0)>0:
            response=f'cached_{key}(request,fragment)'
        else:
            response=f'web_document(render_{key}(request,fragment))'
            control=route['config'].get('cache_control')
            if control:response=header(header(response,'Cache-Control',literal(control)),'Vary','"X-Web-Fragment"')
        if route['guard']:
            response=f'let guarded={route["guard"]}(request) in if guarded.status!=0 then neokit_page_response(guarded) else {response}'
        code.append(f'effect fn page_{key}(request:WebRequest,fragment:bool) -> WebResponse = {response}')
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
    count = len(export_routes)
    render_code = ['import "common.neo"']
    for index,(route,path) in enumerate(export_routes):
        request = 'web_static_request('+literal(path)+')'
        if route['dynamic']:
            request = 'web_routed('+request+','+literal(route['path'])+')'
        render_code.append(f'effect fn neokit_page_{index}() -> Arr u8 _ = render_{route["key"]}({request},false).bytes')
    if error_path.is_file():
        render_code.append('fn neokit_page_404() -> Arr u8 _ = text_NotFound()')
    # One process renders every page as length-prefixed frames.
    frames = [f'neokit_page_{index}()' for index in range(count)]+(['neokit_page_404()'] if error_path.is_file() else [])
    render_code.append('effect fn neokit_render_all() -> unit = '+''.join(
        f'let b{i}={frame} in let w{i}=print_out(concat(int_text(len(b{i})),"\\n",b{i})) in ' for i,frame in enumerate(frames))+'noop()')
    render_code.append('effect fn neokit_render_one(index: i64) -> unit = let body = '+''.join(
        f'if index=={index} then neokit_page_{index}() else ' for index in range(count))+'"" in let written=print_out(body) in noop()')
    render_code.append(f'effect fn main(index: i64) -> unit = if index=={count} then neokit_render_all() else neokit_render_one(index)')
    (generated/'render.neo').write_text('\n'.join(render_code)+'\n')
    snapshots = {}
    not_found_page = None
    if export_routes or target != 'server':
        native_build(generated/'render.neo',out/'render',profile=profile,package_root=package_root)
        result = subprocess.run([str(out/'render'),str(count)],capture_output=True)
        if result.returncode:
            raise BuildError('Static rendering failed:\n'+result.stderr.decode(errors='replace'))
        rendered = read_frames(result.stdout, len(frames))
        for (route,path),data in zip(export_routes,rendered):
            snapshots[path] = data
        if error_path.is_file():
            not_found_page = rendered[-1]
    server = ['import "common.neo"', 'effect fn web_dispatch(request: WebRequest, fragment: bool) -> WebResponse =']
    if dev:
        # Development reads public files and bundled assets from disk per request.
        server.append('let disk=if '+allowed_method(['GET','HEAD'])+' then neokit_dev_read('+literal(str((app/'public').resolve()))+',str_slice(request.path,1,len(request.path))) else web_continue() in if disk.status!=0 then disk else')
        server.append('if str_starts(request.path,"/_web/") then (if !'+allowed_method(['GET','HEAD'])+' then web_method_not_allowed("GET, HEAD") else neokit_dev_asset('+literal(str(out/'assets'))+',str_slice(request.path,6,len(request.path)))) else')
    for url,data in files.items():
        if dev and (url.startswith('/_web/') or url in public):
            continue
        control = IMMUTABLE if url.startswith('/_web/') and url != '/_web/neokit-client.js' else REVALIDATE
        server.append('if str_equal(request.path,'+literal(url)+') then '+embedded_response(data,content_type(url),control)+' else')
    server.append('if str_equal(request.path,"/healthz") then (if !'+allowed_method(['GET','HEAD'])+' then web_method_not_allowed("GET, HEAD") else web_json('+literal('{"ok":true}')+')) else')
    if dev:server.append('if str_equal(request.path,"/_neokit/dev") then web_json(let status=fs_read(env_get("NEOKIT_DEV_STATUS").value) in if status.ok then status.value else '+literal('{"revision":0,"error":null}')+') else')
    dispatch_routes=[(rule['source'],'if str_equal(request.path,'+literal(rule['source'])+') then '+redirect_response(rule)+' else') for rule in redirects]
    for route in routes:
        if route['config'].get('mode') == 'static' and target != 'static':
            for path in route['paths']:
                if path not in snapshots:
                    raise BuildError('Static server routes must have export=true')
                dispatch_routes.append((path,'if str_equal(request.path,'+literal(path)+') then '+embedded_response(snapshots[path],HTML,REVALIDATE)+' else'))
            continue
        response = 'page_'+route['key']+'('+route_request(route)+',fragment)'
        dispatch_routes.append((route['path'],'if '+route_condition(route)+' then (if !'+allowed_method(['GET','HEAD'])+' then web_method_not_allowed("GET, HEAD") else '+response+') else'))
    for url,handler,allowed,streaming in sorted(api_routes,key=lambda r:precedence(r[0])):
        route={'path':url,'dynamic':'[' in url}
        response=('web_reply(200,"","text/event-stream; charset=utf-8")' if url in realtime_routes else 'web_reply(200,"","text/html; charset=utf-8")') if streaming else handler+'('+route_request(route)+')'
        dispatch_routes.append((url,'if '+route_condition(route)+' then (if !'+allowed_method(allowed)+' then web_method_not_allowed('+literal(', '.join(allowed))+') else '+response+') else'))
    server.extend(code for _,code in sorted(dispatch_routes,key=lambda r:precedence(r[0])))
    server.append('neokit_not_found()')
    stream_dispatch=''
    for url,handler,allowed,streaming in sorted(api_routes,key=lambda r:precedence(r[0])):
        if not streaming:continue
        route={'path':url,'dynamic':'[' in url}
        prior=[route_condition({'path':path,'dynamic':'[' in path}) for path in all_paths if precedence(path)<precedence(url)]
        blocked=' || '.join(prior+['str_equal(request.path,"/healthz")'])
        stream_dispatch+='if !head && '+route_condition(route)+' && !('+blocked+') then (if !'+allowed_method(allowed)+' then web_encode_at(web_method_not_allowed('+literal(', '.join(allowed))+'),false,request.stamp) else '+handler+'('+route_request(route)+')) else '
    policy=(lambda response:'neokit_policy('+response+','+literal(csp)+')') if csp is not None else (lambda response:response)
    guard=('let guard='+hooks['before']+'(request) in if guard.status!=0 then web_encode_at('+policy('guard')+',head,request.stamp) else ') if hooks['before'] else ''
    dispatch='web_dispatch(request,str_equal(web_header(raw,"x-web-fragment"),"1"))'
    if hooks['after']:dispatch=hooks['after']+'(request,'+dispatch+')'
    if server_error.is_file():
        server.append('fn web_server_error(response:WebResponse) -> WebResponse = if response.status>=500 then response with {body=text_ServerError(response.status,"The server could not complete this request."),content_type="text/html; charset=utf-8"} else response')
        dispatch='web_server_error('+dispatch+')'
    dispatch=policy(dispatch)
    async_stream_dispatch=''
    for url,handler,allowed,streaming in sorted(api_routes,key=lambda r:precedence(r[0])):
        if not streaming or url not in async_routes:continue
        route={'path':url,'dynamic':'[' in url}
        prior=[route_condition({'path':path,'dynamic':'[' in path}) for path in all_paths if precedence(path)<precedence(url)]
        condition=route_condition(route)
        if prior:condition=condition+' && !('+' || '.join(prior)+')'
        async_stream_dispatch+='if !head && '+condition+' then (if !'+allowed_method(allowed)+' then web_encode_close_at(web_method_not_allowed('+literal(', '.join(allowed))+'),false,request.stamp) else '+handler+'('+route_request(route)+')) else '
    buffered='effect(fs,net,env,time,process) fn web_handle_buffered(raw:Arr u8 n)->Arr u8 _ = let clock=time_unix_ms() in let request=web_request(raw,clock.value) in let head=str_equal(request.method,"HEAD") in '+guard+async_stream_dispatch+'web_encode_close_at('+dispatch+',head,request.stamp)'
    async_dispatch=''
    for url,timeout in sorted(async_routes.items(),key=lambda item:precedence(item[0])):
        route={'path':url,'dynamic':'[' in url}
        prior=[route_condition({'path':path,'dynamic':'[' in path}) for path in all_paths if precedence(path)<precedence(url)]
        condition=route_condition(route)
        prior.append('str_equal(request.path,\"/healthz\")')
        if dev:prior.append('str_equal(request.path,\"/_neokit/dev\")')
        if prior:condition=condition+' && !('+' || '.join(prior)+')'
        async_dispatch+='if '+condition+' then let timer=deadline_after('+str(timeout)+') in let started=http_response_async(raw,function(web_handle_buffered),timer.value) in if started.ok then started.value else web_encode_at(web_reply(503,"Async capacity unavailable","text/plain; charset=utf-8"),head,request.stamp) else '
    if async_routes:
        server.append(buffered)
    observed=server_config.get('access_log',False) or server_config.get('timing',False)
    observed_code=''
    if observed:
        measured='web_server_timing(wire,start.value,end.value)' if server_config.get('timing',False) else 'wire'
        traced='web_trace(raw,'+measured+',start.value,end.value)' if server_config.get('access_log',False) else measured
        observed_code='effect fn observed_handle(raw:Arr u8 n) -> Arr u8 _ = let start=monotonic_ns() in let wire=web_handle(raw) in let end=monotonic_ns() in '+traced+'\n'
    server.extend([
        'effect fn web_handle(raw: Arr u8 n) -> Arr u8 _ = let clock=time_unix_ms() in let request=web_request(raw,clock.value) in let head=str_equal(request.method,"HEAD") in '+async_dispatch+guard+stream_dispatch+'web_encode_at('+dispatch+',head,request.stamp)',
        observed_code+'effect fn main(port: i64{port>0,port<65536}) -> i64 = let configured=http_server_options('+str(grace)+',true) in check configured.ok in '+(f'let events=events_configure({event_channels},{event_history},{event_payload}) in check events.ok in ' if realtime_routes else '')+('let startup='+hooks['startup']+'() in check startup.ok in ' if hooks['startup'] else '')+'serve_effect(port,'+('observed_handle' if observed else 'web_handle')+')',
    ])
    (generated/'server.neo').write_text('\n'.join(server)+'\n')
    if target != 'static':
        native_build(generated/'server.neo',out/'server',profile=profile,package_root=package_root)
    snapshot_dir = out/'snapshots'
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    for path,data in snapshots.items():
        destination = snapshot_file(snapshot_dir, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    if not_found_page is not None and target != 'server':
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir/'404.html').write_bytes(not_found_page)
    if redirects and target != 'server':
        # Netlify and Cloudflare Pages read this file; other hosts need their own rules.
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir/'_redirects').write_text(''.join(f'{rule["source"]} {rule["target"]} {rule["status"]}\n' for rule in redirects))
    if csp is not None and target != 'server':
        # Netlify and Cloudflare Pages apply this file's headers to every exported path.
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir/'_headers').write_text(f'/*\n  Content-Security-Policy: {csp}\n')
    for url,data in files.items():
        destination = snapshot_dir/url.lstrip('/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    omitted = [r['path'] for r in routes if not r['config'].get('export',True)]+[url for url,_,_,_ in api_routes]
    revalidator=None
    if target!='static' and any(r['config'].get('stale_ttl',0)>0 for r in routes):
        revalidator=out/'revalidate';native_build(ROOT/'src/revalidate.neo',revalidator,package_root=package_root)
    migration=build_migration(app,out) if target!='static' and (app/'app/migrate.neo').is_file() else None
    manifest = {'app':str(app),'routes':[{'path':r['path'],'mode':r['config'].get('mode','ssr'),'async':r['config'].get('async',False),'client':r['config'].get('client',False),'export':r['config'].get('export',True),'paths':r['paths']} for r in routes],
                'assets':assets,'files':{url:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for url,data in files.items()},'exported_paths':list(snapshots),'available_export_paths':available_export_paths,'omitted_server_routes':omitted,
                'redirects':[{key:rule[key] for key in ('source','target','status')} for rule in redirects],'not_found_page':not_found_page is not None,
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
