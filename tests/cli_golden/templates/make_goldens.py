#!/usr/bin/env python3
"""Regenerate the template compiler goldens from compiler/templates.py.

Run on the test box from anywhere (bun and node_modules must be installed,
as for any NeoKit build):

    python3 tests/cli_golden/templates/make_goldens.py

It builds every example app and test fixture app with compiler.build.build()
in the release and dev profiles and records each parse() call, the
compile_all() call and each layout substitute() call exactly as build.py
makes them; the build stops before the native compiler would run (paths_fn
is not run either, which only adds export paths). SYNTHETIC below adds edge
cases and invalid templates whose BuildError text must match.

inputs/NAME.json holds the recorded calls. golden/NAME.out is what
compiler/templates.py returns for them, printed in the format driver.neo
prints from cli/templates.neo:

    === CASE
    parse I NAME PATH        then: props K:V ..., type T K:V ..., body LITERAL
                             or:   error MESSAGE
    compile_all              (when every template parsed) then:
    code / CODE / end code, expression NAME LITERAL..., deps NAME DEP...
                             or:   error MESSAGE
    substitute LITERAL       for each recorded substitute() call

where LITERAL is templates.literal() of the text. templates.neo.toml runs
the driver on each input file and compares its output byte for byte:

    $NEO_HOME/neo test tests/cli_golden/templates/templates.neo.toml

Template paths are relative to the repository (or to the fixture app,
prefixed with its name) so the goldens do not depend on the checkout.
"""
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

import compiler.build as build_module  # noqa: E402
from compiler.templates import BuildError, compile_all, compile_template, literal, parse, substitute  # noqa: E402


class StopBuild(Exception):
    pass


class Recorder:
    """The template calls of one build."""

    def __init__(self, app, label, fixture):
        self.app = Path(app).resolve()
        self.label = label
        self.fixture = fixture
        self.parses = []
        self.templates = []
        self.compile = None
        self.substitutes = []

    def where(self, path):
        path = Path(path)
        if not path.is_absolute():
            return str(path)
        if not self.fixture:
            return str(path.relative_to(ROOT))
        return self.label+'/'+str(path.relative_to(self.app))

    def index(self, template):
        return next(i for i, known in enumerate(self.templates) if known is template)


CURRENT = None
REAL_PARSE = build_module.parse
REAL_COMPILE_ALL = build_module.compile_all
REAL_SUBSTITUTE = build_module.substitute


def recording_parse(name, source, path='<template>'):
    CURRENT.parses.append([name, source, CURRENT.where(path)])
    CURRENT.templates.append(None)
    template = REAL_PARSE(name, source, path)
    CURRENT.templates[-1] = template
    return template


def recording_compile_all(templates, components, assets, expressions=None):
    assert all(key == template.name for key, template in components.items())
    CURRENT.compile = {'templates': [CURRENT.index(t) for t in templates],
                       'components': [CURRENT.index(t) for t in components.values()],
                       'assets': [[key, value] for key, value in assets.items()]}
    return REAL_COMPILE_ALL(templates, components, assets, expressions)


def recording_substitute(expression, mapping):
    call = [expression, [[key, value] for key, value in mapping.items()]]
    if call not in CURRENT.substitutes:
        CURRENT.substitutes.append(call)
    return REAL_SUBSTITUTE(expression, mapping)


def stop_build(*args, **kwargs):
    raise StopBuild()


build_module.parse = recording_parse
build_module.compile_all = recording_compile_all
build_module.substitute = recording_substitute
build_module.native_build = stop_build
build_module.resolve_paths = lambda *args, **kwargs: None


def record(app, label, profile, fixture=False):
    global CURRENT
    CURRENT = Recorder(app, label, fixture)
    with tempfile.TemporaryDirectory(prefix='neokit-template-goldens-') as out:
        try:
            build_module.build(app, Path(out)/'out', profile=profile)
        except StopBuild:
            pass
    assert CURRENT.compile is not None, (label, profile)
    case = {'name': label+' '+profile, 'parses': CURRENT.parses, **CURRENT.compile, 'substitutes': CURRENT.substitutes}
    CURRENT = None
    return case


def write_tree(app, files):
    for name, text in files.items():
        path = app/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


# Template-related files of the fixture apps the end-to-end tests write
# (tests/core/audit_features.test.ts, tests/core/consumer.test.ts,
# tests/tools/browser_fixture.ts, tests/extended/dev_css.test.ts, formerly
# tests/*_smoke.py and tests/browser_fixture.py). Route handlers and Neo
# sources are left out: they do not reach the template compiler.
LAYOUT = ('@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html lang="en"><head><title>{{ title }}</title></head>'
          '<body><main id="web-page">{{ content }}</main>{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>')
LAB_PAGE = ('@props(title:text)\n<h1>{{ title }}</h1><p data-version>Template version one</p><label>Page draft<input id="page-draft" value=""></label>'
            '<div data-web-widget="lab" data-web-key="browser-lab" data-web-props=\'{"initial":3}\'><p>Count: <output data-count>3</output></p>'
            '<button data-increment>Add one</button><label>Action title<input data-title value="valid"></label><button data-action>Save action</button>'
            '<p>Action: <output data-pending>idle</output></p><p data-notice role="status"></p><p data-lifecycle></p><p data-widget-version></p></div>')
FIXTURES = {
    'audit_features': {
        'web.toml': '[site]\nurl="https://audit.test"\ndescription="Audit site"\n\n[[redirects]]\nfrom="/old"\nto="/pricing"\n\n'
                    '[[redirects]]\nfrom="/docs-external"\nto="https://docs.example/start"\nstatus=301\nquery=false\n\n'
                    '[security]\ncsp="default-src \'self\'; img-src \'self\' https://cdn.example"\n',
        'app/layout.view': '@props(title:text,content:html,client:bool,mode:text,head:html)\n<!doctype html><html><head><title>{{ title }}</title>{{ head }}</head><body><main id="web-page">{{ content }}</main></body></html>',
        'app/not-found.view': '@props()\n<!doctype html><title>Missing</title><h1>Nothing here</h1>',
        'app/page.toml': 'loader="audit_home"\n[metadata.props]\ndescription="summary"\njsonld="jsonld"\n',
        'app/page.view': '@props(title:text, query:text, posts:Post[], summary:text, jsonld:text)\n@type Post(title:text, href:url, views:int)\n'
                         '<h1>{{ title }}</h1>{% if query %}<p class="q">Results for {{ query }}</p>{% else %}<p class="q">No query</p>{% endif %}'
                         '<ul>{% for post, index in posts %}<li data-rank="{{ index }}"><a href="{{ post.href }}">{{ post.title }}</a>'
                         '{% if post.views >= 1000 %} popular{% elif post.views == 0 %} new{% else %} normal{% endif %}</li>{% endfor %}</ul>'
                         '{% if query == "neo" %}<p>exact</p>{% endif %}',
        'app/(marketing)/pricing/page.toml': 'loader="audit_pricing"\nmode="static"\n',
        'app/(marketing)/pricing/page.view': '@props(title:text)\n<h1>{{ title }}</h1>',
        'app/blog/[slug]/page.toml': 'loader="audit_post"\nmode="static"\npaths_fn="audit_slugs"\n',
        'app/blog/[slug]/page.view': '@props(title:text, slug:text)\n<h1>Post {{ slug }}</h1>',
        'app/account/page.toml': 'loader="audit_account"\nguard="audit_guard"\n',
        'app/account/page.view': '@props(title:text)\n<h1>{{ title }}</h1>',
        'app/search/page.toml': 'loader="audit_search"\ncache_ttl=60\ncache_query=["q"]\n',
        'app/search/page.view': '@props(title:text, q:text, stamp:int)\n<p>{{ q }}:{{ stamp }}</p>',
        'app/private-info/page.toml': 'loader="audit_private"\ncache_control="private, max-age=30"\n',
        'app/private-info/page.view': '@props(title:text)\n<h1>{{ title }}</h1>',
    },
    'consumer': {
        'web.toml': '[site]\nname="Another site"\n',
        'app/layout.view': LAYOUT,
        'app/page.toml': 'loader="other_load"\nclient=false\nmode="ssr"\n',
        'app/page.view': '@props(title:text,count:int,visible:bool)\n<h1 title="{{ title }}">{{ title }}</h1>{% if visible %}<output>{{ count }}</output>{% endif %}',
    },
    'browser_fixture': {
        'web.toml': '[site]\nname="Browser verification lab"\n[server]\naccess_log=true\n',
        'app/layout.view': '@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html lang="en"><head><meta charset="utf-8">'
                           '<meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }}</title><link rel="stylesheet" href="/site.css"></head>'
                           '<body><main id="web-page" tabindex="-1">{{ content }}</main>{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>',
        'app/lab/layout.view': '@props(title:text,content:html,client:bool,mode:text)\n<aside data-web-persist="sidebar"><strong>Shared layout</strong>'
                               '<label>Sidebar draft<input id="sidebar-draft" value="original"></label><nav><a href="/lab/one" data-web-link data-web-prefetch>Page one</a> '
                               '<a href="/lab/two" data-web-link data-web-prefetch>Page two</a></nav></aside><section>{{ content }}</section>',
        **{f'app/lab/{name}/page.toml': f'loader="lab_load_{name}"\nclient=true\nexport=true\ncache_ttl=30\n[metadata]\ndescription="{title} description"\n'
           for name, title in (('one', 'Page one'), ('two', 'Page two'))},
        'app/lab/one/page.view': LAB_PAGE,
        'app/lab/two/page.view': LAB_PAGE,
    },
    'dev_css': {
        'web.toml': '[css]\ntailwind=["site.css"]\n',
        'app/layout.view': '@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html><head><title>{{ title }}</title>'
                           '<link rel="stylesheet" href="@asset(site.css)"></head><body>{{ content }}</body></html>',
        'app/page.toml': 'loader="load"\nmode="ssr"\nclient=false\n',
        'app/page.view': '@props(title:text)\n<h1 class="p-4">{{ title }}</h1>',
        'styles/site.css': '@import "tailwindcss" source(none);\n@import "./detail.css";\n@source "../app";\n@source "../client";\n',
        'styles/detail.css': '.import-test { color: #123456; }',
    },
}

# ---- synthetic cases: one page template "Test" plus the components it may use.
ASSETS = [['site.css', '/_web/site-123.css'], ['client.js', '/_web/client-abc.js']]
HEADER = ('@props(value:text, link:url, rich:html, flag:bool, count:int, items:text[], people:Person[])\n'
          '@type Person(name:text, home:url, bio:html)\n')
COMPONENTS = [('Card', '@props(title:text, content:html)\n<div>{{ title }}{{ content }}</div>'),
              ('Badge', '@props(label:text)\n<b>{{ label }}</b>')]

# Full sources: the header lines themselves.
SOURCES = [
    '', '@props()', 'hello', '@props()\n', '@props() \t\n x', '@props(a:text))\nx', '@props(a:text) x\nx', '@props(a:text,)\nx',
    '@props( : text)\nx', '@props(a:b:c)\nx', '@props(\xe9:text)\nx', '@props(a:Text)\nx', '@props(a:text,a:text)\nx', '@props(_a1:int[])\nx',
    '@props(a:text)\r\nx\r\ny', '@props(a:text)\x1c\nx', '@props(a:text)\xa0　\nx', '@props(a:text)\x0b\nx', '@props(a:url[])\nx',
    '@props()\n@typeX(a:text)\nx', '@props()\n@type  Foo (a:text)\nx', '@props(f:Foo)\n@type\tFoo(a:text)  \n{{ f.a }}', '@props()\n@type Foo()\nx',
    '@props()\n@type Foo(a:text)\n@type Bar(b:Foo)\nx', '@props()\n@type text(a:int)\nx', '@props()\n@type Text(a:int)\nx', '@props()\n@type _Foo(a:int)\nx',
    '@props()\n@type \xc4(a:int)\nx', '@props(f:Foo\xe9)\n@type Foo\xe9(a:int)\n{{ f.a }}', '@props(p:Foo, ps:Foo[], pp:Foo[][])\n@type Foo(a:int)\nx',
    '@props()\n@type Foo(a:int)\n\n@type Bar(b:int)\nx', '@props(f:Foo)\n@type Foo(a:int, a:text)\nx', '@props()\n@type Foo(a:int)',
    '@props(title:text)\n<h1>{{ title }}</h1>', '@props(title:text)\nx', '@props()\n<div class="grid gap-4 md:grid-cols-2">Hello</div>',
    '@props()\n<div class="font-bold">Card</div>', '@props(x:int)\n@props(y:int)\n{{ x }}',
]
# Bodies after HEADER.
BODIES = [
    # HTML validation
    '<p title=\'{{ value }}\' data-x="{{ count }}">{{ value }}</p>', '<p title="{{ value }}" title2={{ value }}>', '<a href="{{ link }}">x</a>',
    '<a href=" {{ link }} ">x</a>', '<a href="{{ link }}{{ link }}">x</a>', '<a href="x{{ link }}">x</a>', '<a HREF="{{ link }}">x</a>',
    '<a href = "{{ link }}">x</a>', '<a href="{{ value }}">x</a>', '<img srcset="{{ link }}">', '<div title="{% if flag %}x{% endif %}">',
    '<div title="a" {{ value }}>', '<div title="{{ rich }}">', '{% for person in people %}<a href="{{ person.home }}">{{ person.name }}</a>{% endfor %}',
    '{% for person in people %}<div title="{{ person.bio }}"></div>{% endfor %}', '{% for person in people %}<a href="{{ person.name }}">x</a>{% endfor %}',
    '<a href="{{ person.home }}">x</a>', '<!-- " {{ value }} " -->', '<script src="/x.js">{{ value }}', '<script src="/x.js">{{ value }}</script>',
    '<script src="/x.js"></script><p>{{ value }}</p>', '<script src="/x.js"/>{{ value }}', '<style>a{}</style>{{ value }}', '<style>{{ value }}',
    '<textarea>{{ value }}</textarea>', '<title>{{ value }}</title>', '<xmp>{{ value }}</xmp>', '<plaintext>{{ value }}', '<script src="x"></SCRIPT >{{ value }}',
    '<style><script src="x"></script>{{ value }}</style>', '<svg><style>{{ value }}</style></svg>', '<script src="">', '<script src>', '<script SRC="a.js"></script>',
    '<script src="a.js" src="">', '<meta http-equiv=" REFRESH " content="0; url = \' javascript:x\'">', '<meta http-equiv="refresh" content="0;url=data:text/html,x">',
    '<meta http-equiv="refresh" content="0;URL=&#x64;ata:x">', '<meta content="0;url=javascript:x" http-equiv="refresh">',
    '<meta http-equiv="refresh" content="0;url=https://x">', '<meta http-equiv="refreshx" content="{{ value }}">', '<meta http-equiv="refresh" content="{{ link }}">',
    '<a href="&#106;avascript:alert(1)">', '<a href="&#x6A;avascript:x">', '<a href="JaVaScRiPt:x">', '<a href=" \tjavascript:x">', '<a href="java\nscript:x">',
    '<a href="vbscript:x">', '<a href="data:x">', '<a href="javaſcript:x">', '<a href="java&#383;cript:x">', '<a href="JAVASCRİPT:x">',
    '<a href="javascrıpt:x">', '<a href=" javascript:x">', '<a href="\x1cjavascript:x">', '<a href="\x0bjavascript:x">', '<a href="\x85javascript:x">',
    '<a href="&Tab;javascript:x">', '<a href="&nbsp;javascript:x">', '<a href="&nbspjavascript:x">', '<a href="&ampjavascript:x">', '<a href="java&#11;script:x">',
    '<a href="java&#65534;script:x">', '<a href="java&#xFDD0;script:x">', '<a href="java&#1;script:x">', '<a href="&#13;javascript:x">', '<a href="&#0;javascript:x">',
    '<a href="&#x110000;javascript:x">', '<a href="&#128;javascript:x">', '<a href="&#99999999999999999999999;javascript:x">', '<a href="&#xD800;javascript:x">',
    '<a href="&NewLine;javascript:x">', '<a href="&abcdefghijklmnopqrstuvwxyzabcdefghij;javascript:x">', '<a href="&notit;javascript:x">',
    '<a href="&#x26;#106;avascript:x">', '<a title="&#123;&#123; value }}">', '<a href="&#123;{ link }}">x</a>', '<a title="&#123;&#123; rich }}">',
    '<a title="&#123;&#37; x %}">', '<div onclick=x>', '<div ONCLICK=x>', '<div on>', '<div one="1">', '<iframe srcdoc>', '<p a= "x>{{ value }}',
    '<p a="x"b="{{ value }}">', '<p/a="{{ value }}">', '<p\ttitle="{{ value }}">', '<p\x0btitle="{{ value }}">', '<p title="{{ value }}"\x0b>',
    '<a href="{{ link }}" href="javascript:x">', '<div data="{{ value }}">', '<div title=x{{ value }}>', '<div title="{{ value }}>',
    '<div title = "{{ value }}" / >', '<div x:title="{{ value }}">', '<div TITLE="{{ value }}">', '<div tİtle="{{ value }}">',
    '<div Ktitle="{{ value }}">', '<div title ="{{ value }}">', '<div  title="{{ value }}"> ', '<p>&#{{ value }};</p><script>x</script>',
    '<p>&#{{ value }}</p><script>x</script>', '<p>&#x;</p><script>x</script>', '<p>&#1a;</p><script>x</script>', '<p>&#12</p><script>x</script>',
    '<p>&x-y.</p><script>x</script>', '<p>&x</p><script>x</script>', '<p>&-</p><script>x</script>', '<p>&</p><script>x</script>', '<p>&amp;</p><script>x</script>',
    '<p>&x.y', '<!DOCTYPE html><p>{{ value }}</p>', '<!doctype html', '<?xml "{{ value }}" ?>', '<![CDATA[ "{{ value }}" ]]>', '<![if "{{ value }}"]>',
    '<![if "{{ value }}">', '<!x "{{ value }}">', '</x "{{ value }}">', '</ "{{ value }}">', '</>{{ value }}', '<!---->{{ value }}', '<!--->{{ value }}',
    '<!-- a --!>{{ value }}', '<!-- "{{ value }}" --!>', '<!-->"{{ value }}"-->', '<!-- unterminated "{{ value }}"', '< p>{{ value }}', 'a < b {{ value }}',
    '1 > 0 {{ value }}', '<p>{{ value }}<', '<1>"{{ value }}"', '<p title="&{{ value }}">', '<title>&#{{ value }};</title><script>x</script>',
    '<textarea>&x</textarea><script>x</script>', '<script src=x></script x="{{ value }}">', '<script src=x></scriptx>"{{ value }}"</script>',
    '<script src=x><!-- </script> --><p>{{ value }}</p>', '<style>"{{ value }}"</style', '<style>x</style/{{ value }}', '<STYLE>x</style>{{ value }}',
    '<sTyLe>{{ value }}</StYlE>', '<noscript>{{ value }}</noscript>', '<iframe>{{ value }}</iframe>', '<noembed>{{ value }}', '<noframes></noframes>{{ value }}',
    '<style/>{{ value }}', '<style />{{ value }}</style>', '<p title="{{ value }}" /x>', '<p title="{{ value }}"/ >', '<p "{{ value }}">',
    '<p =x>', '<p a="1" a=\'{{ value }}\'>', '<img src="{{ link }}" alt=\'{{ value }}\'>', '<form action="{{ link }}"><button formaction="{{ value }}">',
    '<svg><a xlink:href="{{ link }}">x</a></svg>', '<p>é \U0001F600 {{ value }}\t\r</p>', '<p>@asset(site.css) @asset() @asset(site.css @asset(client.js)</p>',
    '<link href="@asset(nope)">', '<p>@asset(a)b)</p>', '',
    # compilation
    '{% if flag %}a{% elif count > 1 %}b{% elif value %}c{% endif %}', '{% if flag %}a{% else %}b{% else %}c{% endif %}', '{% if flag %}a{% endfor %}',
    '{% for x in items %}{% endif %}', '{% for x in items %}{{ x }}{% endfor %}{{ x }}',
    '{% for x, i in items %}{% for y, j in items %}{{ x }}{{ i }}{{ y }}{{ j }}{% endfor %}{% endfor %}',
    '{% for x in items %}{% for x in items %}{% endfor %}{% endfor %}', '{% for x, x in items %}{% endfor %}', '{% for x, value in items %}{% endfor %}',
    '{% for x in value %}{% endfor %}', '{% for x in people %}{{ x.name }}{% endfor %}', '{% for x  in items %}{% endfor %}', '{% for x ,i in items %}{{ i }}{% endfor %}',
    '{%for x in items%}{{ x }}{%endfor%}', '{% for 1x in items %}{% endfor %}', '{% for x in items %}{% else %}{% endfor %}', '{% for x in people.name %}{% endfor %}',
    '{% for p in people %}{% for q in people %}{{ p.name }}{{ q.name }}{% endfor %}{% if p.name == value %}same{% endif %}{% endfor %}',
    '{% if count == 3 %}y{% endif %}', '{% if count != -0 %}y{% endif %}', '{% if count < count %}y{% endif %}', '{% if value == value %}y{% endif %}',
    '{% if value < value %}y{% endif %}', '{% if value == "a\\"b" %}y{% endif %}', '{% if value == "héllo\t" %}y{% endif %}',
    '{% if count == 1234567890123456789 %}y{% endif %}', '{% if count == 123456789012345678 %}y{% endif %}', '{% if count == ١٢ %}y{% endif %}',
    '{% if count == -٣ %}y{% endif %}', '{% if count>=-5 %}y{% endif %}', '{% if count >= - 5 %}y{% endif %}', '{% if value=="x" %}y{% endif %}',
    '{% if value == "" %}y{% endif %}', '{% if value == "it\'s" %}y{% endif %}', '{% if !flag %}y{% endif %}', '{% if ! flag %}y{% endif %}',
    '{% if !items %}y{% endif %}', '{% if !value %}y{% endif %}', '{% if flag==true %}y{% endif %}', '{% if count %}y{% endif %}', '{% if rich %}y{% endif %}',
    '{% if link %}y{% endif %}', '{% if people %}y{% endif %}', '{% if  %}y{% endif %}', '{% iff x %}', '{% endif %}', '{% elif x %}', '{% endcomponent %}',
    '{% endfor %}', '{% else %}', '{{ }}', '{{ a.b }}', '{{ value.x }}', '{{ person }}', '{{ people }}', '{{ items }}', '{{ flag }}{{ count }}{{ rich }}{{ link }}',
    '{{ value\n}}', '{{ välue }}', '{{ vé }}', '{{ value }', 'a {% b', '{%}', '{{}}', '{%%}', '{{ value.}}', '{{ .value }}', '{{ value..x }}',
    '{%   if   flag   %}y{% endif %}', '{%\nif flag\n%}y{% endif %}', '{% if\tflag %}y{% endif %}', '{% if flag %}{% if count > 0 %}a{% endif %}',
    '{% if flag == flag %}y{% endif %}', '{% if count == value %}y{% endif %}', '{% if value != "x" %}y{% elif !flag %}z{% endif %}',
    '{% if (flag) %}y{% endif %}', '{% if flag and count %}y{% endif %}', '{% if _x %}y{% endif %}', '{% if value.x == "a" %}y{% endif %}',
    '{% component Card title=value %}<p>{{ value }}</p>{% endcomponent %}', '{% component Card %}x{% endcomponent %}',
    '{% component Card title=value content=rich %}x{% endcomponent %}', '{% component Card title %}x{% endcomponent %}',
    '{% component Card title=value title=value %}x{% endcomponent %}', '{% component Card title=count %}x{% endcomponent %}',
    '{% component Badge label=value %}x{% endcomponent %}', '{% component Card title=value %}x', '{% component %}', '{% component Nope %}',
    '{% component Card title=nope %}x{% endcomponent %}', '{% component Card title=value %}{% component Card title=value %}inner{% endcomponent %}{% endcomponent %}',
    '{% component Card title=value %}{% endcomponent %}', '{% component Card title=value %}x{% endfor %}', '{% include Card title=value content=rich %}',
    '{% include %}', '{% include Card title=value extra=value %}', '{% include Card title value %}', '{% include Card title=nope %}',
    '{% include Card title=value title=value %}', '{% include Badge label=value %}{% include Badge label=value %}', '{% include Badge label=count %}',
    '{% include Badge label=rich %}', '{% include Badge   label=value   %}', '{% include Badge label= value %}', '{% include Badge label==value %}',
    '{% for x in items %}{% include Badge label=x %}{% endfor %}', '{% for p in people %}{% include Badge label=p.name %}{% endfor %}',
    '{% for p, n in people %}{% component Card title=p.name %}{{ n }}{% endcomponent %}{% endfor %}', '{% include Nope %}', '{% include Badge label=x.y %}',
    '\t\r\n\x00\x7f\\"x"', 'x\n{% if flag %}\n{{ value }}\n{% endif %}\n',
]
GROUPS = [
    ('recursive pair', [('A', '@props()\n{% include B %}'), ('B', '@props()\n{% include A %}')]),
    ('recursive self', [('A', '@props()\n{% include A %}')]),
    ('recursive three', [('A', '@props()\n{% include B %}{% include C %}'), ('B', '@props()\n{% include C %}'),
                         ('C', '@props(content:html)\n{% component A %}x{% endcomponent %}')]),
    ('component block cycle', [('Box', '@props(content:html)\n{% component Box %}{{ content }}{% endcomponent %}')]),
    ('diamond', [('A', '@props()\n{% include B %}{% include C %}'), ('B', '@props()\n{% include D %}'), ('C', '@props()\n{% include D %}'), ('D', '@props()\nd')]),
    ('duplicate names', [('A', '@props()\na1'), ('A', '@props()\n{% include B %}'), ('B', '@props()\nb')]),
    ('component missing', [('A', '@props()\n{% include B %}')]),
]
SUBSTITUTES = [
    ['concat("a\\"p_title",p_title,"\\\\",p_title)', [['p_title', 'X']]],
    ['p_content.bytes p_content p_contents x.p_content p_content_x ép_content p_contenté (p_content)', [['p_content.bytes', 'C'], ['p_content', 'web_html(C)']]],
    ['"line\\\nbreak p_title" p_title', [['p_title', 'T']]],
    ['"unterminated p_title', [['p_title', 'T']]],
    ['abc ab cd abcd', [['ab', '1'], ['cd', '2'], ['abc', '3']]],
    ['a.b a.c a x.a a_b', [['a', 'A'], ['a.b', 'B']]],
    ['p_title "" p_title "\\"" p_title', [['p_title', '"q"']]],
    ['no names here', []],
]


def synthetic():
    cases = []
    components = [[name, source, 'synthetic/components/'+name+'.view'] for name, source in COMPONENTS]

    def page(name, source):
        cases.append({'name': name, 'parses': components+[['Test', source, 'synthetic/'+name+'.view']],
                      'templates': list(range(len(components)+1)), 'components': list(range(len(components))),
                      'assets': ASSETS, 'substitutes': []})
    for i, source in enumerate(SOURCES):
        page('source %d' % i, source)
    for i, body in enumerate(BODIES):
        page('body %d' % i, HEADER+body)
    for name, views in GROUPS:
        cases.append({'name': name, 'parses': [[view, source, 'synthetic/'+view+'.view'] for view, source in views],
                      'templates': list(range(len(views))), 'components': list(range(len(views))), 'assets': [], 'substitutes': []})
    cases.append({'name': 'substitute', 'parses': [], 'templates': [], 'components': [], 'assets': [], 'substitutes': SUBSTITUTES})
    return cases


def render(case):
    """compiler/templates.py's results for one case, as driver.neo prints them."""
    out = ['=== '+case['name']]
    templates = []
    for i, (name, source, path) in enumerate(case['parses']):
        out.append(f'parse {i} {name} {path}')
        try:
            template = parse(name, source, path)
        except BuildError as error:
            out.append('error '+str(error))
            templates.append(None)
            continue
        out.append('props'+''.join(' '+key+':'+kind for key, kind in template.props.items()))
        for type_name, fields in template.types.items():
            out.append('type '+type_name+''.join(' '+key+':'+kind for key, kind in fields.items()))
        out.append('body '+literal(template.body))
        templates.append(template)
    used = case['templates']+case['components']
    if case['templates'] and all(templates[i] is not None for i in used):
        components = {templates[i].name: templates[i] for i in case['components']}
        assets = dict(case['assets'])
        expressions = {}
        out.append('compile_all')
        try:
            code = compile_all([templates[i] for i in case['templates']], components, assets, expressions)
        except BuildError as error:
            out.append('error '+str(error))
        else:
            out.append('code')
            out.append(code+'end code')
            for name, (_, expression) in expressions.items():
                out.append('expression '+name+' '+literal(expression))
            for i in case['templates']:
                deps = compile_template(templates[i], components, assets)[1]
                out.append('deps '+templates[i].name+''.join(' '+dep for dep in deps))
    for expression, mapping in case['substitutes']:
        out.append('substitute '+literal(substitute(expression, dict(mapping))))
    return '\n'.join(out)+'\n'


def apps():
    found = [path.parent for path in sorted((ROOT/'examples').glob('*/web.toml'))]
    return found+[ROOT/'tests'/name for name in ('catalog_fixture', 'realtime_fixture', 'realtime_limits_fixture')]


def main():
    groups = {}
    for profile in ('release', 'dev'):
        groups['apps-'+profile] = [record(app, app.name, profile) for app in apps()]
    fixtures = []
    for label, files in FIXTURES.items():
        for profile in ('release', 'dev'):
            # Inside the checkout, so Tailwind resolves NeoKit's node_modules.
            (ROOT/'build').mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='template-fixture-', dir=ROOT/'build') as temporary:
                app = Path(temporary)/label
                write_tree(app, files)
                fixtures.append(record(app, label, profile, fixture=True))
    groups['fixtures'] = fixtures
    groups['synthetic'] = synthetic()
    (HERE/'inputs').mkdir(exist_ok=True)
    (HERE/'golden').mkdir(exist_ok=True)
    manifest = ['# Generated by make_goldens.py from compiler/templates.py; do not edit.',
                '# Run: $NEO_HOME/neo test tests/cli_golden/templates/templates.neo.toml', '']
    for name, cases in groups.items():
        (HERE/'inputs'/(name+'.json')).write_text(json.dumps({'cases': cases}, ensure_ascii=False, indent=0)+'\n')
        (HERE/'golden'/(name+'.out')).write_text(''.join(render(case) for case in cases))
        manifest += ['[[test]]', f'name = "templates {name}"', 'file = "driver.neo"',
                     f'args = ["tests/cli_golden/templates/inputs/{name}.json"]', f'stdout-file = "golden/{name}.out"', '']
        print(name, len(cases), 'cases')
    (HERE/'templates.neo.toml').write_text('\n'.join(manifest))


if __name__ == '__main__':
    main()
