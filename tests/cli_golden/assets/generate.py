"""Regenerate the metadata and generated-asset goldens from compiler/assets.py,
the reference.

Run from anywhere on the test box (Python 3.12 with Pillow; the script turns
off the optional brotli module, as the native port has no Brotli), then compare with
the native port (cli/assets.neo; responsive images need libvips's vips and
vipsheader on PATH):

    python3 tests/cli_golden/assets/generate.py
    NEO_HOME=/root/neo-dev/neo-feat $NEO_HOME/neo test tests/cli_golden/assets/assets.neo.toml

Inputs are every app in examples/ and the test fixtures (tests/*_fixture),
the apps the TypeScript tests write (../routes/ts_apps.py), the image apps
under apps/ (whose pictures this script draws with Pillow) and the edge
cases below. The script writes the derived inputs to cases/, the expected
output to golden/ and the manifest assets.neo.toml. driver.neo runs one mode
per case and prints:

- metadata: for each page, metadata_props(page), then metadata(web, page,
  skip=those props) and dynamic_metadata(web, page, template props);
- generated: generated_assets(app, web, routes, cache) for each app. Text
  files are printed whole; an image is printed as its URL with the content
  hash replaced by <hash> (checked against the bytes), its format and its
  decoded size, because Pillow and libvips encode different bytes. A second
  run with the same cache must give the same result;
- compressible: compressible() of content types;
- compressed: whether compressed() keeps a gzip form ("gzip", which the
  driver also decodes back to the input) and brotli_compressed() (always
  none: the reference runs without brotli, the native build has none).
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent/'routes'))
from PIL import Image  # noqa: E402
import compiler.assets  # noqa: E402
from compiler.assets import brotli_compressed, compressed, compressible, dynamic_metadata, generated_assets, metadata, metadata_props  # noqa: E402
compiler.assets._brotli = None  # As without the optional module: the native build has no Brotli encoder.
from compiler.build import CONTENT_TYPES, content_type, discover  # noqa: E402
from compiler.routes import route_path  # noqa: E402
from compiler.templates import BuildError, parse  # noqa: E402
from ts_apps import TS_APPS  # noqa: E402

CASES = HERE/'cases'
GOLDEN = HERE/'golden'
APPS = HERE/'apps'
IMAGES = APPS/'images'

WEB_EDGE = '[site]\nurl="https://edge.test/"\ndescription="Edge <site> & \\"more\\""\nrobots="index"\nog_title=42\nog_image=["a", 1, true, {k="v"}]\ncanonical="https://edge.test/x?a=1&b=\'q\'"\n'
# label -> (web.toml, page.toml, template props "name:kind,...")
METADATA_CASES = {
    'site only': (WEB_EDGE, 'loader="x"\n', 'title:text'),
    'page overrides and skip': (WEB_EDGE, 'loader="x"\n[metadata]\ndescription="Page desc"\nog_description=false\n[metadata.props]\nrobots="r"\n', 'title:text,r:text'),
    'og values': ('', '[metadata]\nog_title="T <1>"\nog_description=-7\nog_image={a=[1,"b\'c"],d=false}\n', ''),
    'og quotes repr': ('', '[metadata]\nog_title=["it\'s", "say \\"hi\\"", "both \'\\""]\n', ''),
    'og unicode repr': ('', '[metadata]\nog_title=["caf\u00e9", "\\u0007", "\\u00a0", "\\u2028"]\n', ''),
    'escape all': ('[site]\ndescription="& < > \\" \'"\n', '', ''),
    'canonical empty': ('[site]\ncanonical=""\n', '', ''),
    'canonical zero': ('[site]\ncanonical=0\n', '', ''),
    'canonical false': ('[site]\ncanonical=false\n', '', ''),
    'canonical empty list': ('[site]\ncanonical=[]\n', '', ''),
    'canonical number': ('[site]\ncanonical=5\n', '', ''),
    'canonical ftp': ('[site]\ncanonical="ftp://x"\n', '', ''),
    'canonical http': ('', '[metadata]\ncanonical="http://x.test/<a>"\n', ''),
    'canonical skipped': ('', '[metadata]\ncanonical="ftp://x"\n[metadata.props]\ncanonical="u"\n', 'u:url'),
    'robots number': ('[site]\nrobots=1\n', '', ''),
    'description list': ('', '[metadata]\ndescription=[]\n', ''),
    'description list skipped': ('', '[metadata]\ndescription=[]\n[metadata.props]\ndescription="d"\n', 'd:text'),
    'props key ignored': ('[site]\nprops="x"\n', '[metadata]\nprops={description="d"}\n', 'd:text'),
    'site props': ('[site]\nprops="x"\n', '', ''),
    'title is not metadata': ('[site]\ntitle="Site"\nname="N"\n', '[metadata]\ntitle="T"\n', ''),
    'props not table': ('', '[metadata]\nprops=1\n', ''),
    'props empty': ('', '[metadata]\nprops={}\n', ''),
    'dynamic home': (WEB_EDGE, '[metadata.props]\ndescription="summary"\njsonld="jsonld"\n', 'title:text,summary:text,jsonld:text'),
    'dynamic all': (WEB_EDGE, '[metadata.props]\nog_title="t"\nog_description="t"\nog_image="u"\ncanonical="u"\nrobots="t"\n', 't:text,u:url'),
    'dynamic origin escaped': ('[site]\nurl="https://a.test/p?a=1&b=2//"\n', '[metadata.props]\nog_image="u"\n', 'u:url'),
    'dynamic no origin': ('', '[metadata.props]\nog_title="t"\nog_image="u"\n', 't:text,u:url'),
    'dynamic canonical no origin': ('[site]\nurl="/"\n', '[metadata.props]\ncanonical="u"\n', 'u:url'),
    'dynamic unsupported': (WEB_EDGE, '[metadata.props]\ntitle="t"\n', 't:text'),
    'dynamic wrong kind': (WEB_EDGE, '[metadata.props]\nog_image="t"\n', 't:text'),
    'dynamic prop not text': (WEB_EDGE, '[metadata.props]\nog_image=1\n', 't:text'),
    'dynamic missing prop': (WEB_EDGE, '[metadata.props]\ndescription="missing"\n', 't:text'),
    'dynamic list prop': (WEB_EDGE, '[metadata.props]\ndescription="items"\n', 'items:text[]'),
    'dynamic second fails': (WEB_EDGE, '[metadata.props]\ndescription="t"\ntitle="t"\n', 't:text'),
    'dynamic jsonld url': (WEB_EDGE, '[metadata.props]\njsonld="u"\n', 'u:url'),
}
ORIGIN_CASES = {
    'origin trailing slashes': '[site]\nurl="https://x.test///"\n',
    'origin http': '[site]\nurl="http://x.test"\n',
    'origin with path': '[site]\nurl="https://x.test/base/"\n',
    'origin with ampersand': '[site]\nurl="https://x.test/?a=1&b=2"\n',
    'origin javascript': '[site]\nurl="javascript:alert(1)"\n',
    'origin quote': '[site]\nurl="https://a.test/\\"x"\n',
    'origin newline': '[site]\nurl="https://a.test\\n"\n',
    'origin angle': '[site]\nurl="https://a.test/<x>"\n',
    'origin ftp': '[site]\nurl="ftp://x.test"\n',
    'origin slashes only': '[site]\nurl="///"\n',
    'origin scheme only': '[site]\nurl="https:///"\n',
    'origin empty': '[site]\nurl=""\n',
    'origin unicode': '[site]\nurl="https://caf\u00e9.test"\n',
    'images not table': 'images=1\n',
    'images empty': '[images]\n',
    'images no files': '[images]\nfiles=[]\nwidths=[0]\nformats=["gif"]\n',
    'images empty string files': '[images]\nfiles=""\n',
}
IMAGE_MAIN = '[site]\nurl="https://images.test"\n[images]\nfiles=["photo.png","halves.png","tall.png","wide.png","palette.png","rot6.jpg","rot3.jpg","rot8.png","rot5.webp","anim.gif","sub/deep.png","two.dots.png","photo.png"]\nwidths=[16,40,56,1000,40]\nformats=["webp","jpeg","png"]\n'
IMAGE_CASES = {
    'images main': IMAGE_MAIN,
    'images defaults': '[images]\nfiles=["photo.png"]\n',
    'images large width': '[images]\nfiles=["halves.png"]\nwidths=[8192,1]\nformats=["png","png"]\n',
    'images huge': '[images]\nfiles=["huge.png"]\n',
    'images traversal': '[images]\nfiles=["../../secret.png"]\n',
    'images outside': '[images]\nfiles=["../web.toml"]\n',
    'images absolute': '[images]\nfiles=["/etc/hostname"]\n',
    'images directory': '[images]\nfiles=[""]\n',
    'images subdirectory': '[images]\nfiles=["sub"]\n',
    'images missing': '[images]\nfiles=["missing.png"]\n',
    'images second fails': '[images]\nfiles=["photo.png","missing.png"]\nwidths=[16]\n',
    'images width zero': '[images]\nfiles=["photo.png"]\nwidths=[0]\n',
    'images width too large': '[images]\nfiles=["photo.png"]\nwidths=[8193]\n',
    'images widths empty': '[images]\nfiles=["photo.png"]\nwidths=[]\n',
    'images width float': '[images]\nfiles=["photo.png"]\nwidths=[1.5]\n',
    'images width boolean': '[images]\nfiles=["photo.png"]\nwidths=[true]\n',
    'images widths text': '[images]\nfiles=["photo.png"]\nwidths="320"\n',
    'images format gif': '[images]\nfiles=["photo.png"]\nformats=["gif"]\n',
    'images formats empty': '[images]\nfiles=["photo.png"]\nformats=[]\n',
    'images formats text': '[images]\nfiles=["photo.png"]\nformats="webp"\n',
    'images format upper': '[images]\nfiles=["photo.png"]\nformats=["WEBP"]\n',
    'images string files': '[images]\nfiles="ab"\n',
    'images table files': '[images]\nfiles={x=1}\n',
}
TYPES = ['text/', 'Text/html', 'text', 'image/svg+xml', 'image/svgz', 'image/png', 'application/javascript; charset=utf-8',
         'application/json-seq', 'application/jsonx', 'application/xml-dtd', 'application/xhtml+xml', 'application/wasm',
         'application/manifest+json', 'application/octet-stream', 'application/x-javascript', '', ' text/html', 'font/woff2']


def draw_images():
    """The image fixtures, drawn the same way on every run."""
    public = IMAGES/'public'
    (public/'sub').mkdir(parents=True, exist_ok=True)
    (IMAGES/'web.toml').write_text(IMAGE_MAIN)
    Image.new('RGB', (100, 50), 'red').save(public/'photo.png')
    Image.new('RGB', (80, 25), 'green').save(public/'halves.png')
    Image.new('L', (31, 200), 128).save(public/'tall.png')
    Image.new('RGBA', (400, 3), (0, 0, 255, 128)).save(public/'wide.png')
    palette = Image.new('P', (50, 50), 1)
    palette.putpalette([0, 0, 0, 255, 200, 0]+[0]*762)
    palette.save(public/'palette.png')
    for name, size, orientation in (('rot6.jpg', (120, 40), 6), ('rot3.jpg', (120, 40), 3), ('rot8.png', (60, 30), 8),
                                    ('rot5.webp', (50, 20), 5)):
        image = Image.new('RGB', size, 'purple')
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(public/name, exif=exif)
    first, second = Image.new('P', (30, 20), 0), Image.new('P', (30, 20), 1)
    first.save(public/'anim.gif', save_all=True, append_images=[second])
    Image.new('RGB', (64, 48), 'gray').save(public/'sub'/'deep.png')
    Image.new('RGB', (20, 20), 'white').save(public/'two.dots.png')
    Image.new('1', (6325, 6325)).save(public/'huge.png')
    for app, names in (('robots', ('robots.txt',)), ('sitemap', ('sitemap.xml',)), ('both', ('robots.txt', 'sitemap.xml'))):
        (APPS/app/'public').mkdir(parents=True, exist_ok=True)
        for name in names:
            (APPS/app/'public'/name).write_text('User-agent: *\nDisallow: /\n' if name == 'robots.txt' else '<urlset/>\n')


def app_dirs():
    found = [ROOT/'examples'/name for name in sorted(os.listdir(ROOT/'examples')) if (ROOT/'examples'/name/'app').is_dir()]
    return found+[ROOT/'tests'/name for name in sorted(os.listdir(ROOT/'tests'))
                  if name.endswith('_fixture') and (ROOT/'tests'/name/'app').is_dir()]


def outcome(function, *args):
    try:
        return function(*args)
    except BuildError as error:
        return BuildError(str(error))


def ts_exported(files):
    """discover()'s exported paths for a TypeScript app, without its templates."""
    paths = []
    for path, text in sorted(files.items()):
        if path.startswith('app/') and path.endswith('/page.toml') or path == 'app/page.toml':
            config = tomllib.loads(text)
            route = route_path(Path('/app')/path[4:].rpartition('page.toml')[0], Path('/app'))
            if config.get('export', True) and not config.get('guard'):
                paths += config.get('paths', [] if '[' in route else [route])
    return paths


def metadata_output(label, web, page, props):
    out = '=== '+label+'\n'
    found = outcome(metadata_props, page)
    out += 'props: '+(('error: '+str(found)) if isinstance(found, BuildError) else ', '.join(f'{k}={v if isinstance(v, str) else "<not text>"}' for k, v in found.items()))+'\n'
    head = outcome(metadata, web, page, set() if isinstance(found, BuildError) else set(found))
    out += 'static: '+(('error: '+str(head)) if isinstance(head, BuildError) else head)+'\n'
    dynamic = outcome(dynamic_metadata, web, page, props)
    if isinstance(dynamic, BuildError):
        return out+'dynamic: error: '+str(dynamic)+'\n'
    parts, jsonld = dynamic
    return out+'dynamic: '+' | '.join(parts)+(' | jsonld' if jsonld else '')+'\n'


def image_format(data):
    if data.startswith(b'\x89PNG'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return 'unknown'


def generated_output(label, app, web, paths):
    with tempfile.TemporaryDirectory() as cache:
        first = outcome(generated_assets, app, web, [{'config': {}, 'paths': paths}], Path(cache))
        if isinstance(first, BuildError):
            return '=== '+label+'\nerror: '+str(first)+'\n'
        second = generated_assets(app, web, [{'config': {}, 'paths': paths}], Path(cache))
    files, bindings = first
    out = '=== '+label+'\n'
    masked = {}
    for url, data in files.items():
        if url.startswith('/_web/images/'):
            stem, _, suffix = url.rpartition('.')
            digest = stem[-12:]
            masked[url] = stem[:-12]+('<hash>' if digest == hashlib.sha256(data).hexdigest()[:12] else '<badhash>')+'.'+suffix
            with Image.open(io.BytesIO(data)) as image:
                out += f'file {masked[url]} {image_format(data)} {image.width}x{image.height}\n'
        else:
            out += 'file '+url+'\n'+data.decode()+'\n'
    for key, url in bindings.items():
        out += 'binding '+key+' '+masked.get(url, url)+'\n'
    return out+'second run: '+('same' if second == first else 'different')+'\n'


def write_chunks(name, chunks):
    (CASES/name).write_text(''.join('#--- '+label+'\n'+text for label, text in chunks), encoding='utf-8', newline='\n')
    return 'tests/cli_golden/assets/cases/'+name


def write_lines(name, lines):
    (CASES/name).write_text(''.join(line+'\n' for line in lines), encoding='utf-8', newline='\n')
    return 'tests/cli_golden/assets/cases/'+name


def main():
    CASES.mkdir(exist_ok=True)
    GOLDEN.mkdir(exist_ok=True)
    for stale in [*CASES.rglob('*'), *GOLDEN.glob('*.out')]:
        if stale.is_file():
            stale.unlink()
    draw_images()
    entries = []

    def case(name, mode, inputs, text):
        golden = name.replace(' ', '-')+'.out'
        (GOLDEN/golden).write_text(text, encoding='utf-8', newline='\n')
        entries.append('[[test]]\nname = '+json.dumps('assets '+name)+'\nfile = "driver.neo"\nargs = '
                       + json.dumps([mode, *inputs])+'\nstdout-file = '+json.dumps('golden/'+golden)+'\n')

    # metadata: every page of every app, then the TypeScript apps and the edge cases.
    pages = []
    for app in app_dirs():
        web = (app/'web.toml').read_text() if (app/'web.toml').is_file() else ''
        for path in sorted((app/'app').rglob('page.toml')):
            view = path.parent/'page.view'
            props = parse('Page', view.read_text(), view).props if view.is_file() else {}
            pages.append((path.relative_to(ROOT).as_posix(), web, path.read_text(), props))
    for name, files in TS_APPS.items():
        for path, text in sorted(files.items()):
            if path.endswith('page.toml'):
                view = files.get(path[:-len('page.toml')]+'page.view')
                props = parse('Page', view, path).props if view else {}
                pages.append(('ts:'+name+'/'+path, files.get('web.toml', ''), text, props))
    for label, (web, page, props) in METADATA_CASES.items():
        pages.append((label, web, page, dict(item.split(':') for item in props.split(',') if item)))
    chunks = [(label, web+'#-- page\n'+page+'#-- props\n'+''.join(f'{k}\t{v}\n' for k, v in props.items()))
              for label, web, page, props in pages]
    case('metadata', 'metadata', [write_chunks('metadata.cases', chunks)],
         ''.join(metadata_output(label, tomllib.loads(web), tomllib.loads(page), props) for label, web, page, props in pages))

    # generated_assets: the apps with their exported routes, then origins and images.
    apps = []
    for app in app_dirs():
        web = (app/'web.toml').read_text() if (app/'web.toml').is_file() else ''
        routes = discover(app)
        paths = [p for route in routes if route['config'].get('export', True) for p in route['paths']]
        apps.append((app.relative_to(ROOT).as_posix(), app.relative_to(ROOT).as_posix(), web, paths))
    for name, files in TS_APPS.items():
        apps.append(('ts:'+name, 'tests/cli_golden/assets/apps/none', files.get('web.toml', ''), ts_exported(files)))
    edge_paths = ['/b', '/a&b', '/', '/b', '/caf\u00e9', '/x"y']
    for label, web in ORIGIN_CASES.items():
        apps.append((label, 'tests/cli_golden/assets/apps/none', web, edge_paths))
    for name in ('robots', 'sitemap'):
        apps.append(('public '+name, 'tests/cli_golden/assets/apps/'+name, '[site]\nurl="https://public.test"\n', ['/']))
    apps.append(('public both', 'tests/cli_golden/assets/apps/both', '[site]\nurl="https://public.test"\n', ['/']))
    apps.append(('public none', 'tests/cli_golden/assets/apps/none', '[site]\nurl="https://public.test"\n', []))
    for label, web in IMAGE_CASES.items():
        apps.append((label, 'tests/cli_golden/assets/apps/images', web, ['/']))
    chunks = [(label, folder+'\n#-- web\n'+web+'#-- paths\n'+''.join(p+'\n' for p in paths)) for label, folder, web, paths in apps]
    case('generated_assets', 'generated', [write_chunks('generated.cases', chunks)],
         ''.join(generated_output(label, (ROOT/folder).resolve(), tomllib.loads(web), paths) for label, folder, web, paths in apps))

    # compressible: build.py's content types, those of every public file, and edge strings.
    urls = []
    for app in app_dirs():
        if (app/'public').is_dir():
            urls += sorted('/'+p.relative_to(app/'public').as_posix() for p in (app/'public').rglob('*') if p.is_file())
    types = list(dict.fromkeys([*CONTENT_TYPES.values(), *(content_type(url) for url in urls),
                                *(content_type('/x'+suffix) for suffix in ('.txt', '.xml', '.map', '.webmanifest', '.ico', '.pdf', '.wasm', '.mjs', '.csv', '.md', '.woff', '.zip')),
                                *TYPES]))
    case('compressible', 'compressible', [write_lines('types.lines', types)],
         ''.join(t+'\t'+str(int(compressible(t)))+'\n' for t in types))

    # compressed: every public file and client module, and byte fixtures around the thresholds.
    data = CASES/'data'
    data.mkdir(exist_ok=True)
    noise = b''.join(hashlib.sha256(str(i).encode()).digest() for i in range(256))
    fixtures = {'empty.bin': b'', 'a511.txt': b'a'*511, 'a512.txt': b'a'*512, 'a513.txt': b'a'*513,
                'noise512.bin': noise[:512], 'noise8192.bin': noise, 'noise-tail-zeros.bin': noise[:520]+b'\0'*80,
                'noise-half.bin': noise[:300]+b'x'*300, 'text600.txt': (b'NeoKit precompresses public text. '*20)[:600],
                'two-bytes-512.bin': b'ab'*256}
    for name, content in fixtures.items():
        (data/name).write_bytes(content)
    files = [p.relative_to(ROOT).as_posix() for app in app_dirs() if (app/'public').is_dir() for p in sorted((app/'public').rglob('*')) if p.is_file()]
    files += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT/'client').glob('*.js') if not p.name.endswith('.test.js'))
    files += ['tests/cli_golden/assets/cases/data/'+name for name in fixtures]
    lines = []
    for name in files:
        content = (ROOT/name).read_bytes()
        packed = compressed(content)
        lines.append(name+'\t'+('gzip' if packed else 'none')+'\tbr '+('none' if brotli_compressed(content, packed) is None else 'some')+'\n')
    case('compressed', 'compressed', files, ''.join(lines))

    # The fs_test_ cases of tests/cli_assets_test.neo, which need effects and so
    # cannot run under `neo test --unit-only`; effects.neo prints one line each.
    effects = ['missing image tools are named', 'public sitemap and robots take precedence', 'sitemap, robots and origin rules',
               'images are hashed, resized and traversal is rejected', 'image variants are cached by content']
    (GOLDEN/'effects.out').write_text(''.join('ok '+name+'\n' for name in effects), encoding='utf-8', newline='\n')
    entries.append('[[test]]\nname = "assets effectful unit cases"\nfile = "effects.neo"\nstdout-file = "golden/effects.out"\n')

    header =('# Generated by generate.py from compiler/assets.py; do not edit.\n'
              '# Run with the Neo 0.2.6 branch build: neo test tests/cli_golden/assets/assets.neo.toml\n\n')
    (HERE/'assets.neo.toml').write_text(header+'\n'.join(entries), encoding='utf-8', newline='\n')
    print(f'{len(entries)} cases, {len(pages)} pages, {len(apps)} apps, {len(types)} types, {len(files)} files')


if __name__ == '__main__':
    main()
