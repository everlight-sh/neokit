"""Small typed HTML template compiler. Output is ordinary Neo source."""
from dataclasses import dataclass
from html.parser import HTMLParser
import re


class BuildError(ValueError):
    pass


def literal(value):
    data = value.encode('utf-8') if isinstance(value, str) else value
    escapes = {10:r'\n', 13:r'\r', 9:r'\t', 34:r'\"', 92:r'\\'}
    return '"'+''.join(escapes.get(b, chr(b) if 32 <= b < 127 else f'\\x{b:02x}') for b in data)+'"'


TYPES = {'text':'Arr u8 _', 'int':'i64', 'bool':'bool', 'html':'WebHtml', 'url':'WebURL'}
TYPES.update({kind+'[]':'Arr '+native+' _' for kind,native in list(TYPES.items()) if kind!='url'})
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
TOKEN = re.compile(r'({{.*?}}|{%.*?%})', re.S)


@dataclass
class Template:
    name: str
    props: dict
    body: str
    path: str


def parse(name, source, path='<template>'):
    header, separator, body = source.partition('\n')
    match = re.fullmatch(r'@props\((.*)\)\s*', header)
    if not separator or not match:
        raise BuildError(f'{path}: first line must be @props(name: text, ...)')
    props = {}
    for field in match[1].split(','):
        if not field.strip():
            continue
        parts = [p.strip() for p in field.split(':')]
        if len(parts) != 2 or not NAME.fullmatch(parts[0]) or parts[1] not in TYPES or parts[0] in props:
            raise BuildError(f'{path}: invalid/duplicate prop {field!r}; types: text, int, bool, html, url, text[], int[], bool[], html[]')
        props[parts[0]] = parts[1]
    template = Template(name, props, body, str(path))
    validate_html(template)
    return template


def validate_html(template):
    # Interpolation can be HTML text or a quoted attribute value, never part of
    # a tag/attribute name. Escaping alone cannot make dynamic syntax safe.
    in_tag = False
    quote = None
    cursor = 0
    for match in TOKEN.finditer(template.body):
        for char in template.body[cursor:match.start()]:
            if in_tag:
                if quote:
                    if char == quote:
                        quote = None
                elif char in ('"', "'"):
                    quote = char
                elif char == '>':
                    in_tag = False
            elif char == '<':
                in_tag = True
        if in_tag and (quote is None or match[0].startswith('{%')):
            raise BuildError(f'{template.path}: dynamic tag/attribute syntax is not supported')
        cursor = match.end()

    local_types = dict(template.props)
    for item,source in re.findall(r'{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%}', template.body):
        kind = local_types.get(source, '')
        if kind.endswith('[]'): local_types[item] = kind[:-2]

    class Validator(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.raw = []

        def handle_starttag(self, tag, attrs):
            raw = self.get_starttag_text()
            if tag in ('script', 'style'):
                self.raw.append(tag)
            if tag == 'script' and not dict(attrs).get('src'):
                raise BuildError(f'{template.path}: use external JavaScript modules, not inline scripts')
            for key, value in attrs:
                if key.startswith('on') or key == 'srcdoc':
                    raise BuildError(f'{template.path}: executable HTML attributes are not supported')
                if value and re.match(r'\s*(javascript|vbscript):', value, re.I):
                    raise BuildError(f'{template.path}: executable URLs are not supported')
                if value and ('{{' in value or '{%' in value):
                    if key in ('style','srcset') or key.startswith('on'):
                        raise BuildError(f'{template.path}: dynamic {key} is unsupported')
                    if key in ('href','src','action','formaction'):
                        names = re.findall(r'{{\s*(\w+)\s*}}', value)
                        if len(names)!=1 or value.strip()!='{{ '+names[0]+' }}' and value.strip()!='{{'+names[0]+'}}' or local_types.get(names[0])!='url':
                            raise BuildError(f'{template.path}: dynamic {key} requires one url prop')
                    if '{%' in value or not re.search(r'\b'+re.escape(key)+r'\s*=\s*[\"\']', raw):
                        raise BuildError(f'{template.path}: interpolate only inside quoted attribute values')
                    for name in re.findall(r'{{\s*(\w+)\s*}}', value):
                        if local_types.get(name) == 'html':
                            raise BuildError(f'{template.path}: HTML props cannot appear in attributes')

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

        def handle_endtag(self, tag):
            if self.raw and self.raw[-1] == tag:
                self.raw.pop()

        def handle_data(self, data):
            if self.raw and ('{{' in data or '{%' in data):
                raise BuildError(f'{template.path}: interpolation inside script/style is not supported')

        def handle_comment(self, data):
            if '{{' in data or '{%' in data:
                raise BuildError(f'{template.path}: interpolation inside HTML comments is not supported')

    Validator().feed(template.body)


def compile_template(template, components, assets):
    tokens = TOKEN.split(template.body)
    dependencies = []
    position = 0
    helpers = []
    kinds = dict(template.props)
    locals_ = {}

    def variable(name):
        if name not in kinds:
            raise BuildError(f'{template.path}: unknown prop {name!r}')
        return locals_.get(name, 'p_'+name)

    def join(parts):
        return literal('') if not parts else parts[0] if len(parts) == 1 else 'concat('+','.join(parts)+')'

    def sequence(nested=False):
        nonlocal position
        parts = []
        while position < len(tokens):
            token = tokens[position]
            position += 1
            if token.startswith('{{'):
                name = token[2:-2].strip()
                value = variable(name)
                kind = kinds[name]
                if kind.endswith('[]'): raise BuildError(f'{template.path}: render list props with a for loop')
                parts.append('html_escape('+value+')' if kind == 'text' else 'int_text('+value+')' if kind == 'int'
                             else '(if '+value+' then "true" else "false")' if kind == 'bool' else 'html_escape('+value+'.value)' if kind=='url' else value+'.bytes')
            elif token.startswith('{%'):
                directive = token[2:-2].strip()
                if directive in ('else', 'endif', 'endfor', 'endcomponent'):
                    if not nested:
                        raise BuildError(f'{template.path}: unexpected {directive}')
                    return join(parts), directive
                if directive.startswith('if '):
                    name = directive[3:].strip()
                    negative = name.startswith('!')
                    name = name[1:] if negative else name
                    condition = variable(name)
                    if kinds[name] != 'bool':
                        raise BuildError(f'{template.path}: if requires a bool prop')
                    yes, end = sequence(True)
                    no = literal('')
                    if end == 'else':
                        no, end = sequence(True)
                    if end != 'endif':
                        raise BuildError(f'{template.path}: missing endif')
                    parts.append('(if '+('!' if negative else '')+condition+' then '+yes+' else '+no+')')
                elif directive.startswith('for '):
                    match = re.fullmatch(r'for ([A-Za-z_]\w*) in ([A-Za-z_]\w*)',directive)
                    if not match: raise BuildError(f'{template.path}: use for item in list_prop')
                    item,source = match.groups()
                    array = variable(source)
                    if not kinds[source].endswith('[]') or item in kinds:
                        raise BuildError(f'{template.path}: for requires a list and a fresh item name')
                    captures=list(locals_.items())
                    helper = f'text_{template.name}_loop_{len(helpers)}'
                    helpers.append('')
                    slot = len(helpers)-1
                    kinds[item] = kinds[source][:-2]
                    locals_[item] = 'item_'+item
                    body,end = sequence(True)
                    del kinds[item];del locals_[item]
                    if end!='endfor': raise BuildError(f'{template.path}: missing endfor')
                    args = ','.join('p_'+n+': '+TYPES[k] for n,k in template.props.items())
                    args += ''.join(','+native+': '+TYPES[kinds[name]] for name,native in captures)
                    call = ','.join(['p_'+n for n in template.props]+[native for _,native in captures])
                    # A balanced tree avoids quadratic string copying for long lists.
                    helpers[slot] = (f'fn {helper}({args},at:i64,count:i64) -> Arr u8 _ = '
                        f'if count<=0 then "" else if count==1 then check at>=0 && at<len({array}) in '
                        f'let item_{item}={array}[at] in {body} else let half=count/2 in '
                        f'concat({helper}({call},at,half),{helper}({call},at+half,count-half))\n')
                    parts.append(f'{helper}({call},0,len({array}))')
                elif directive.startswith('component '):
                    words=directive.split();name=words[1] if len(words)>1 else ''
                    if name not in components or components[name].props.get('content')!='html':
                        raise BuildError(f'{template.path}: component blocks require a content:html slot')
                    bindings={}
                    for word in words[2:]:
                        key,eq,value=word.partition('=')
                        if not eq or key in bindings or key=='content': raise BuildError(f'{template.path}: invalid component binding')
                        variable(value);bindings[key]=value
                    component=components[name]
                    if set(bindings)!=(set(component.props)-{'content'}): raise BuildError(f'{template.path}: missing component props')
                    for key,value in bindings.items():
                        if kinds[value]!=component.props[key]: raise BuildError(f'{template.path}: incompatible component prop {key}')
                    content,end=sequence(True)
                    if end!='endcomponent': raise BuildError(f'{template.path}: missing endcomponent')
                    dependencies.append(name)
                    parts.append('view_'+name+'('+','.join('web_html('+content+')' if prop=='content' else variable(bindings[prop]) for prop in component.props)+').bytes')
                elif directive.startswith('include '):
                    words = directive.split()
                    if len(words) < 2:
                        raise BuildError(f'{template.path}: include requires a component name')
                    name = words[1]
                    if name not in components:
                        raise BuildError(f'{template.path}: unknown component {name}')
                    component = components[name]
                    bindings = {}
                    for word in words[2:]:
                        key, equal, value = word.partition('=')
                        if not equal or key in bindings:
                            raise BuildError(f'{template.path}: component bindings use prop=local_prop')
                        variable(value)
                        bindings[key] = value
                    if set(bindings) != set(component.props):
                        raise BuildError(f'{template.path}: {name} requires props {list(component.props)}')
                    for key, value in bindings.items():
                        if kinds[value] != component.props[key]:
                            raise BuildError(f'{template.path}: incompatible prop {key} for {name}')
                    dependencies.append(name)
                    parts.append('view_'+name+'('+','.join(variable(bindings[p]) for p in component.props)+').bytes')
                else:
                    raise BuildError(f'{template.path}: unsupported directive {directive!r}')
            else:
                if '{{' in token or '{%' in token:
                    raise BuildError(f'{template.path}: unterminated template expression')
                def asset(match):
                    key = match[1]
                    if key not in assets:
                        raise BuildError(f'{template.path}: missing asset {key}')
                    return assets[key]
                token = re.sub(r'@asset\(([^)]+)\)', asset, token)
                if token:
                    parts.append(literal(token))
        return join(parts), None

    expression, end = sequence()
    args = ','.join('p_'+name+': '+TYPES[kind] for name, kind in template.props.items())
    call = ','.join('p_'+name for name in template.props)
    # text_* is the bare concat, so a text_* call inside another template's concat
    # is inlined and flattened by the compiler instead of copied as a finished string.
    code = (''.join(helpers)+f'fn text_{template.name}({args}) -> Arr u8 _ = {expression}\n'
            f'fn view_{template.name}({args}) -> WebHtml = web_html(text_{template.name}({call}))\n')
    return code, dependencies, expression


def compile_all(templates, components, assets, expressions=None):
    output = []
    edges = {}
    for template in templates:
        code, deps, expression = compile_template(template, components, assets)
        output.append(code)
        edges[template.name] = deps
        if expressions is not None:
            expressions[template.name] = (template, expression)
    def visit(name, active):
        if name in active:
            raise BuildError('Recursive template component: '+' -> '.join([*active, name]))
        for child in edges.get(name, []):
            visit(child, [*active, name])
    for name in edges:
        visit(name, [])
    return '\n'.join(output)
