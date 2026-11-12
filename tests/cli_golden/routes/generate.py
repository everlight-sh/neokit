"""Regenerate the route and typed-action goldens from compiler/routes.py and
compiler/actions.py, the reference.

Run from anywhere on the test box, then compare with the native port
(cli/routes.neo, cli/actions.neo):

    python3 tests/cli_golden/routes/generate.py
    NEO_HOME=/root/neo-dev/neo-feat $NEO_HOME/neo test tests/cli_golden/routes/routes.neo.toml

Inputs are every app in examples/ and the test fixtures (tests/*_fixture),
the apps the TypeScript tests write (ts_apps.py), and the edge cases below.
The script writes the derived inputs to cases/, the expected output to
golden/ and the manifest routes.neo.toml. Each case runs driver.neo in one
mode on some inputs and compares its output byte for byte:

- folders: route_path() of every page/route folder (relative to app/, or a
  "folder<TAB>base" pair of paths), "INPUT<TAB>RESULT" or "error: MESSAGE";
- canonical, shape: canonical_path() as 0/1 and shape() of each path;
- matches: matches(pattern, path) as 0/1 for "pattern<TAB>path" lines;
- precedence: each path's rank tuple, then the paths sorted by precedence();
- methods, actions: methods(config, label) (and with build.py's typed-action
  default ["POST"]), and action_adapter(config, KEY) of every route.toml and
  of the TOML snippets in cases/*.cases.
"""
import json
import os
from pathlib import Path
import sys
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from compiler.actions import action_adapter  # noqa: E402
from compiler.routes import canonical_path, matches, methods, precedence, route_path, shape  # noqa: E402
from compiler.templates import BuildError  # noqa: E402
from ts_apps import TS_APPS  # noqa: E402

CASES = HERE/'cases'
GOLDEN = HERE/'golden'
KEY = 'Action_0123456789ab'

FOLDERS = ['', '.', 'a/./b', 'a//b', 'a/b/', '(g)', '(g)/(h)/x', '(bad group', '(a b)', '()', '(x-y_1)', '(café)',
           '..', 'a/..', '...', '....', 'a b', 'café', '[id]', '[id]/[id]', '[id]/x/[id]', '[...slug]',
           '[...slug]/more', '[...slug]/a b', 'a b/[...slug]/x', '[[...slug]]', '[[...slug]]/x', '[x]/[...y]', '[x]/[[...x]]',
           '[1x]', '[]', '[x', 'x]', '[...]', '[[...]]', '[[x]]', '[..x]', '[....x]', '[x-y]', '[_]', '[_1]', '[...a]]',
           'a.b', '.well-known', 'feed.xml', '-', '_', '(g)/[a]/(h)/[b]', '[a]/(g)/[a]', 'a/[[...b]]/(g)', '[...a]/(g)',
           '(g)/[...a]/(h)/b', 'api/v1.2/[id]', 'docs/[[...slug]]', 'org/[org]/items/[id]']
FOLDER_PAIRS = [('/app/x', '/app'), ('/app', '/app'), ('/app/', '/app'), ('/app//x/./y/', '/app/'), ('/other/x', '/app'),
                ('/app/x', '/app/x/y'), ('rel/x', 'rel'), ('rel', 'rel'), ('/app/x', 'app'), ('app/x', '/app'),
                ('/app/(g)/[id]', '/app'), ('/app/..', '/app'), ('/appx/y', '/app'), ('/', '/')]
CANONICAL = ['/', '/a', '/a/b-c_d', '/feed.xml', '/.well-known/security.txt', '', 'a', '/a/', '//a', '/a//b', '/a/../b',
             '/./a', '/a b', '/a?b', '/..', '/.', '/...', '/a/.', '/a/..', '/a.', '/.a', '/-', '/_', '/café', '/A-Z',
             '/a\tb', '/%2e', '/a#b', '/[x]', '/~', '/a/b/c/d/e', '/a.b/c..d', '/0', '//', '///', '/a/b/', '\\a', '/a\\b']
MATCHES = [('/docs/[[...slug]]', '/docs'), ('/docs/[[...slug]]', '/docs/a/b'), ('/docs/[...slug]', '/docs'),
           ('/docs/[[...slug]]', '/docs//a'), ('/blog/[slug]', '/blog/hello.world'), ('/', '/'), ('//', '/'), ('', '/'),
           ('', '/a'), ('/[[...a]]', '/'), ('/[[...a]]', '/x'), ('/[[...a]]', '/x/y/z'), ('/[...a]', '/'), ('/[...a]', '/x/y'),
           ('a/[x', '/a/y'), ('/a', '/a/'), ('/./a', '/a'), ('/./a', '/./a'), ('/a.b', '/axb'), ('/a.b', '/a.b'), ('/a', '/A'),
           ('/[a]/[b]', '/x/y'), ('/[a]/[b]', '/x'), ('/x/[[...a]]/y/[...b]', '/x/y/z'), ('/x/[[...a]]/y/[...b]', '/x/y'),
           ('/x/[[...a]]/y/[...b]', '/x/1/y/2/y/3'), ('/x/[[...a]]/[[...b]]', '/x'), ('/x/[[...a]]/[[...b]]', '/x/1/2/3'),
           ('/[x]', '/a/b'), ('/a/b', '/a/b/'), ('/café', '/café'), ('/(g)/a', '/(g)/a'), ('/a+b', '/a+b'),
           ('/a/[...b]/c/[...d]', '/a/1/c/2/c/3'), ('/a/[...b]/c/[...d]', '/a/c/c'), ('/[[x]]', '/y'), ('/[]', '/y'),
           ('/a//[x]', '/a/1'), ('/a/[x]/', '/a/1'), ('a', '/a'), ('/a', 'a'), ('/a', ''), ('/[x]', '/.'), ('/[x]', '/..'),
           ('/[...x]', '/a/../b'), ('/.well-known/[x]', '/.well-known/security.txt'), ('/x', '/x/.'), ('/a?b', '/a?b')]
SHAPES = ['/org/[org]/items/[id]', '/docs/[[...slug]]', '/a/[]', '/a/[b', '/[a/b]/c', '/[[x]]', '[...]', '/x[y]z[...w]',
          '/[[...a]', '/[[...a]]]', '/[...a]]', '/[[[...a]]]', '/[...[x]]', '/[.x]', '/[..x]', '/[a][b]', '/[[...]]',
          '/[[....]]', '/]', '/[', '', '/café/[é]','/[[...a]b]]', '/[[...a]]/[b]/[...c]', '/[[[x]]]',
          '/a]/[b', '/[...]]', '/[[...x]/y]]', '/x/[[...]]]', '/[[...a]]x]]']
PRECEDENCE = ['/', '/docs', '/docs/new', '/docs/[id]', '/docs/[...slug]', '/docs/[[...slug]]', '/[x]', '/[...x]', '/[[...x]]',
              '/a/[b]/c', '/a/b/[c]', '/a/[[...b]]', '', 'x', '/[x', '/[...x]/y', '/z', '/Z', '/a/b', '/a/b']

# TOML snippets for methods() and action_adapter(). Every case avoids the
# configs where Python raises something other than BuildError.
METHODS_CASES = {
    'no methods': 'handler="h"\n',
    'GET': 'methods=["GET"]\n',
    'POST': 'methods=["POST"]\n',
    'GET HEAD': 'methods=["GET","HEAD"]\n',
    'HEAD GET': 'methods=["HEAD","GET"]\n',
    'HEAD': 'methods=["HEAD"]\n',
    'all': 'methods=["OPTIONS","DELETE","PATCH","PUT","POST","GET"]\n',
    'all with HEAD': 'methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS","HEAD"]\n',
    'empty': 'methods=[]\n',
    'text': 'methods="GET"\n',
    'duplicate': 'methods=["GET","POST","GET"]\n',
    'lowercase': 'methods=["get"]\n',
    'CONNECT': 'methods=["CONNECT"]\n',
    'integer': 'methods=[1]\n',
    'boolean': 'methods=[true]\n',
    'number': 'methods=1\n',
    'table': 'methods={GET=true}\n',
    'unknown then integer': 'methods=["FOO",1]\n',
    'typed default': 'handler="h"\ninput_type="T"\n[input]\nx="text"\n',
    'typed with GET': 'handler="h"\ninput_type="T"\nmethods=["GET"]\n[input]\nx="text"\n',
    'typed empty methods': 'handler="h"\ninput_type="T"\nmethods=[]\n[input]\nx="text"\n',
}
ACTION_CASES = {
    'no input': 'handler="h"\n',
    'input type without input': 'handler="h"\ninput_type="T"\n',
    'empty schema': 'handler="h"\ninput_type="T"\n[input]\n',
    'inline empty schema': 'handler="h"\ninput_type="T"\ninput={}\n',
    'defaults': 'handler="h"\ninput_type="T"\n[input]\nname="text"\nn="int"\nflag="bool"\n',
    'csrf false': 'handler="h"\ninput_type="T"\ncsrf=false\n[input]\nname="text"\n',
    'csrf true': 'handler="h"\ninput_type="T"\ncsrf=true\n[input]\nname="text"\n',
    'optional fields': 'handler="save"\ninput_type="Form"\n[input]\na={type="text",required=false}\nb={type="int",required=false,min=-5}\nc={type="bool",required=false}\n',
    'bool ignores bounds': 'handler="h"\ninput_type="T"\n[input]\nx={type="bool",min="z",max=[]}\n',
    'text limits': 'handler="h"\ninput_type="T"\n[input]\nt={type="text",min=0,max=1048576}\n',
    'int limits': 'handler="h"\ninput_type="T"\n[input]\ni={type="int",min=-9223372036854775808,max=9223372036854775807}\n',
    'equal bounds': 'handler="h"\ninput_type="T"\n[input]\ni={type="int",min=7,max=7}\nt={type="text",min=3,max=3}\n',
    'only max': 'handler="h"\ninput_type="T"\n[input]\nt={type="text",max=9}\ni={type="int",max=-1}\n',
    'subtables': 'handler="h"\ninput_type="T"\n[input.title]\ntype="text"\nmin=2\n[input.count]\ntype="int"\n',
    'dotted keys': 'handler="h"\ninput_type="T"\n[input]\ntitle.type="text"\ntitle.max=10\nok="bool"\n',
    'identifier names': 'handler="h"\ninput_type="T_1"\n[input]\n_x="text"\na1="int"\nZ="bool"\n',
    'input not table': 'handler="h"\ninput_type="T"\ninput="x"\n',
    'input array': 'handler="h"\ninput_type="T"\ninput=[]\n',
    'input array of tables': 'handler="h"\ninput_type="T"\n[[input]]\nx="text"\n',
    'missing input_type': 'handler="h"\n[input]\nx="text"\n',
    'empty input_type': 'handler="h"\ninput_type=""\n[input]\nx="text"\n',
    'bad input_type': 'handler="h"\ninput_type="not valid"\n[input]\n',
    'input_type with newline': 'handler="h"\ninput_type="T\\n"\n[input]\n',
    'bad field name': 'handler="h"\ninput_type="T"\n[input]\n"bad name"="text"\n',
    'digit field name': 'handler="h"\ninput_type="T"\n[input]\n1x="text"\n',
    'unknown kind': 'handler="h"\ninput_type="T"\n[input]\nx="float"\n',
    'integer kind': 'handler="h"\ninput_type="T"\n[input]\nx=1\n',
    'no type': 'handler="h"\ninput_type="T"\n[input]\nx={required=true}\n',
    'list type': 'handler="h"\ninput_type="T"\n[input]\nx={type=["text"]}\n',
    'array field': 'handler="h"\ninput_type="T"\n[input]\nx=["text"]\n',
    'required text': 'handler="h"\ninput_type="T"\n[input]\nx={type="text",required="yes"}\n',
    'required integer': 'handler="h"\ninput_type="T"\n[input]\nx={type="int",required=1}\n',
    'negative text min': 'handler="h"\ninput_type="T"\n[input]\nx={type="text",min=-1}\n',
    'text max too large': 'handler="h"\ninput_type="T"\n[input]\nx={type="text",max=1048577}\n',
    'inverted bounds': 'handler="h"\ninput_type="T"\n[input]\nx={type="int",min=5,max=4}\n',
    'float min': 'handler="h"\ninput_type="T"\n[input]\nx={type="int",min=1.5}\n',
    'boolean max': 'handler="h"\ninput_type="T"\n[input]\nx={type="int",max=true}\n',
    'text min above default max': 'handler="h"\ninput_type="T"\n[input]\nx={type="text",min=5000}\n',
    'second field fails': 'handler="h"\ninput_type="T"\n[input]\na="text"\nb="nope"\n',
    'csrf text': 'handler="h"\ninput_type="T"\ncsrf="no"\n[input]\nx="text"\n',
    'field error before csrf': 'handler="h"\ninput_type="T"\ncsrf="no"\n[input]\nx="nope"\n',
    'unicode field name': 'handler="h"\ninput_type="T"\n[input]\n"café"="text"\n',
}


def app_dirs():
    found = [ROOT/'examples'/name for name in sorted(os.listdir(ROOT/'examples')) if (ROOT/'examples'/name/'app').is_dir()]
    return found+[ROOT/'tests'/name for name in sorted(os.listdir(ROOT/'tests'))
                  if name.endswith('_fixture') and (ROOT/'tests'/name/'app').is_dir()]


def config_files():
    """Every page.toml and route.toml as (label, text): repository files first, then the TS apps."""
    found = []
    for app in app_dirs():
        for path in sorted((app/'app').rglob('*.toml')):
            if path.name in ('page.toml', 'route.toml'):
                found.append((path.relative_to(ROOT).as_posix(), path.read_text()))
    for name, files in TS_APPS.items():
        for path, text in files.items():
            if path.startswith('app/') and path.endswith(('/page.toml', '/route.toml')):
                found.append(('ts:'+name+'/'+path, text))
    return found


def folder_of(label):
    """The folder below app/ of a page.toml or route.toml label."""
    inner = label.split('/app/', 1)[1] if '/app/' in label else label
    return inner.rsplit('/', 1)[0] if '/' in inner else ''


def web_configs():
    found = [tomllib.loads((app/'web.toml').read_text()) for app in app_dirs() if (app/'web.toml').is_file()]
    return found+[tomllib.loads(files['web.toml']) for files in TS_APPS.values() if 'web.toml' in files]


def unique(items):
    return list(dict.fromkeys(items))


def samples(pattern):
    """Concrete paths made from a route pattern's parameters."""
    out = []
    for fill in (('v', 'a/b', ''), ('x.y', 'a', 'a/b/c')):
        parts = []
        for part in pattern.split('/'):
            if part.startswith('[[...'):
                part = fill[2]
            elif part.startswith('[...'):
                part = fill[1]
            elif part.startswith('['):
                part = fill[0]
            if part:
                parts.append(part)
        out.append('/'+'/'.join(parts))
    return out


def outcome(function, *args):
    try:
        return function(*args)
    except BuildError as error:
        return BuildError(str(error))


def shown(value):
    return 'error: '+str(value) if isinstance(value, BuildError) else value


def write_lines(name, lines):
    (CASES/name).write_text(''.join(line+'\n' for line in lines), encoding='utf-8', newline='\n')
    return 'tests/cli_golden/routes/cases/'+name


def write_chunks(name, chunks):
    (CASES/name).write_text(''.join('#--- '+label+'\n'+text for label, text in chunks), encoding='utf-8', newline='\n')
    return 'tests/cli_golden/routes/cases/'+name


def folder_result(line):
    if '\t' in line:
        folder, base = line.split('\t')
        try:
            return shown(outcome(route_path, Path(folder), Path(base)))
        except ValueError as error:
            return 'error: '+str(error)
    return shown(outcome(route_path, Path('/app')/line, Path('/app')))


def config_output(label, text, mode):
    config = tomllib.loads(text)
    if mode == 'methods':
        out = '=== '+label+'\n'
        result = outcome(methods, config, label)
        out += 'methods: '+(shown(result) if isinstance(result, BuildError) else ', '.join(result))+'\n'
        if 'input' in config:
            typed = outcome(methods, {**config, 'methods': config.get('methods', ['POST'])}, label)
            out += 'typed methods: '+(shown(typed) if isinstance(typed, BuildError) else ', '.join(typed))+'\n'
        return out
    result = outcome(action_adapter, config, KEY)
    return '=== '+label+'\n'+('None' if result is None else shown(result))+'\n'


def main():
    CASES.mkdir(exist_ok=True)
    GOLDEN.mkdir(exist_ok=True)
    for stale in [*CASES.glob('*'), *GOLDEN.glob('*.out')]:
        stale.unlink()
    configs = config_files()
    folders = unique([folder_of(label) for label, _ in configs]+FOLDERS)
    patterns = unique([result for result in (outcome(route_path, Path('/app')/f, Path('/app')) for f in folders)
                       if not isinstance(result, BuildError)])
    exported = []
    for label, text in configs:
        paths = tomllib.loads(text).get('paths', [])
        exported += [path for path in paths if isinstance(path, str)] if isinstance(paths, list) else []
    for web in web_configs():
        exported += [rule['from'] for rule in web.get('redirects', []) if isinstance(rule.get('from'), str)]
    public = []
    for app in app_dirs():
        if (app/'public').is_dir():
            public += sorted('/'+p.relative_to(app/'public').as_posix() for p in (app/'public').rglob('*') if p.is_file())
    candidates = unique(patterns+exported+[s for p in patterns for s in samples(p)])
    # Each app's patterns against its own routes, export paths and samples.
    pairs = []
    for app in unique(label.split('/app/', 1)[0] for label, _ in configs):
        own = [(label, text) for label, text in configs if label.startswith(app+'/app/')]
        routes = unique([r for r in (outcome(route_path, Path('/app')/folder_of(label), Path('/app')) for label, _ in own)
                         if not isinstance(r, BuildError)])
        listed = [p for _, text in own for p in tomllib.loads(text).get('paths', []) if isinstance(p, str)]
        pairs += [(p, c) for p in routes for c in unique(routes+listed+[s for r in routes for s in samples(r)])]
    pairs = unique(pairs+MATCHES)
    ordered = unique(patterns+exported+PRECEDENCE)

    entries = []

    def case(name, mode, inputs, text):
        golden = name.replace(' ', '-')+'.out'
        (GOLDEN/golden).write_text(text, encoding='utf-8', newline='\n')
        entries.append('[[test]]\nname = '+json.dumps('routes '+name)+'\nfile = "driver.neo"\nargs = '
                       + json.dumps([mode, *inputs])+'\nstdout-file = '+json.dumps('golden/'+golden)+'\n')

    folder_lines = folders+['\t'.join(pair) for pair in FOLDER_PAIRS]
    case('route_path', 'folders', [write_lines('folders.lines', folder_lines)],
         ''.join(line+'\t'+folder_result(line)+'\n' for line in folder_lines))
    canonical = unique(candidates+public+CANONICAL)
    case('canonical_path', 'canonical', [write_lines('canonical.lines', canonical)],
         ''.join(path+'\t'+str(int(canonical_path(path)))+'\n' for path in canonical))
    case('matches', 'matches', [write_lines('matches.lines', ['\t'.join(pair) for pair in pairs])],
         ''.join(a+'\t'+b+'\t'+str(int(matches(a, b)))+'\n' for a, b in pairs))
    order = sorted(range(len(ordered)), key=lambda i: precedence(ordered[i]))
    case('precedence', 'precedence', [write_lines('precedence.lines', ordered)],
         ''.join(p+'\t'+','.join(map(str, precedence(p)[0]))+'\n' for p in ordered)+'--- sorted\n'
         + ''.join(ordered[i]+'\n' for i in order))
    shapes = unique(patterns+public+SHAPES)
    case('shape', 'shape', [write_lines('shape.lines', shapes)], ''.join(p+'\t'+shape(p)+'\n' for p in shapes))
    repository = [label for label, _ in configs if not label.startswith('ts:') and label.endswith('route.toml')]
    typescript = [(label, text) for label, text in configs if label.startswith('ts:') and label.endswith('route.toml')]
    for mode, edge in (('methods', METHODS_CASES), ('actions', ACTION_CASES)):
        inputs = repository+[write_chunks('ts-routes.cases', typescript),
                             write_chunks(mode+'.cases', [(label, text) for label, text in edge.items()])]
        text = ''.join(config_output(label, (ROOT/label).read_text(), mode) for label in repository)
        text += ''.join(config_output(label, body, mode) for label, body in typescript)
        text += ''.join(config_output(label, body, mode) for label, body in edge.items())
        case(mode, mode, inputs, text)
    header = ('# Generated by generate.py from compiler/routes.py and compiler/actions.py; do not edit.\n'
              '# Run with the Neo 0.2.6 branch build: neo test tests/cli_golden/routes/routes.neo.toml\n\n')
    (HERE/'routes.neo.toml').write_text(header+'\n'.join(entries), encoding='utf-8', newline='\n')
    print(f'{len(entries)} cases, {len(folder_lines)} folders, {len(canonical)} paths, {len(pairs)} pairs')


if __name__ == '__main__':
    main()
