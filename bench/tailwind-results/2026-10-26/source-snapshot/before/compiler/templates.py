"""Small typed HTML template compiler. Output is ordinary Axon source."""
from dataclasses import dataclass
from html.parser import HTMLParser
import re


class BuildError(ValueError):
    pass


def literal(value):
    data = value.encode('utf-8') if isinstance(value, str) else value
    escapes = {10:r'\n', 13:r'\r', 9:r'\t', 34:r'\"', 92:r'\\'}
    return '"'+''.join(escapes.get(b, chr(b) if 32 <= b < 127 else f'\\x{b:02x}') for b in data)+'"'


TYPES = {'text':'Arr u8 _', 'int':'i64', 'bool':'bool', 'html':'WebHtml'}
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
            raise BuildError(f'{path}: invalid/duplicate prop {field!r}; types: text, int, bool, html')
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
                    if key in ('href', 'src', 'action', 'formaction', 'style', 'srcset') or key.startswith('on'):
                        raise BuildError(f'{template.path}: dynamic {key} requires a future typed URL/CSS API')
                    if '{%' in value or not re.search(r'\b'+re.escape(key)+r'\s*=\s*[\"\']', raw):
                        raise BuildError(f'{template.path}: interpolate only inside quoted attribute values')
                    for name in re.findall(r'{{\s*(\w+)\s*}}', value):
                        if template.props.get(name) == 'html':
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

    def variable(name):
        if name not in template.props:
            raise BuildError(f'{template.path}: unknown prop {name!r}')
        return 'p_'+name

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
                kind = template.props[name]
                parts.append('html_escape('+value+')' if kind == 'text' else 'int_text('+value+')' if kind == 'int'
                             else '(if '+value+' then "true" else "false")' if kind == 'bool' else value+'.bytes')
            elif token.startswith('{%'):
                directive = token[2:-2].strip()
                if directive in ('else', 'endif'):
                    if not nested:
                        raise BuildError(f'{template.path}: unexpected {directive}')
                    return join(parts), directive
                if directive.startswith('if '):
                    name = directive[3:].strip()
                    negative = name.startswith('!')
                    name = name[1:] if negative else name
                    condition = variable(name)
                    if template.props[name] != 'bool':
                        raise BuildError(f'{template.path}: if requires a bool prop')
                    yes, end = sequence(True)
                    no = literal('')
                    if end == 'else':
                        no, end = sequence(True)
                    if end != 'endif':
                        raise BuildError(f'{template.path}: missing endif')
                    parts.append('(if '+('!' if negative else '')+condition+' then '+yes+' else '+no+')')
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
                        if template.props[value] != component.props[key]:
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
    return f'fn view_{template.name}({args}) -> WebHtml = web_html({expression})\n', dependencies


def compile_all(templates, components, assets):
    output = []
    edges = {}
    for template in templates:
        code, deps = compile_template(template, components, assets)
        output.append(code)
        edges[template.name] = deps
    def visit(name, active):
        if name in active:
            raise BuildError('Recursive template component: '+' -> '.join([*active, name]))
        for child in edges.get(name, []):
            visit(child, [*active, name])
    for name in edges:
        visit(name, [])
    return '\n'.join(output)
