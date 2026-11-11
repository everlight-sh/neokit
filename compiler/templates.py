"""Small typed HTML template compiler. Output is ordinary Neo source."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


class BuildError(ValueError):
    pass


def literal(value):
    data = value.encode('utf-8') if isinstance(value, str) else value
    escapes = {10:r'\n', 13:r'\r', 9:r'\t', 34:r'\"', 92:r'\\'}
    return '"'+''.join(escapes.get(b, chr(b) if 32 <= b < 127 else f'\\x{b:02x}') for b in data)+'"'


SCALARS = {'text':'Arr u8 _', 'int':'i64', 'bool':'bool', 'html':'WebHtml', 'url':'WebURL'}
TYPES = dict(SCALARS)
TYPES.update({kind+'[]':'Arr '+native+' _' for kind,native in SCALARS.items() if kind!='url'})
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
PATH = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*')
TOKEN = re.compile(r'({{.*?}}|{%.*?%})', re.S)
STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
# Attributes a browser resolves as URLs. A dynamic value must be one url prop,
# because escaping cannot stop a javascript: or data: scheme.
URL_ATTRIBUTES = {'href', 'src', 'action', 'formaction', 'xlink:href', 'data', 'poster', 'cite',
                  'background', 'ping', 'manifest', 'codebase', 'archive', 'longdesc', 'usemap',
                  'icon', 'profile', 'lowsrc', 'dynsrc'}
EXECUTABLE_URL = re.compile(r'\s*(javascript|vbscript):', re.I)
TYPE_HELP = 'types: text, int, bool, html, url, text[], int[], bool[], html[], and @type records'


@dataclass
class Template:
    name: str
    props: dict
    body: str
    path: str
    types: dict = field(default_factory=dict)


def substitute(expression, mapping):
    """Replace identifiers in generated Neo code, never inside string literals."""
    if not mapping:
        return expression
    names = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(r'(?<![\w.])(?:'+'|'.join(re.escape(name) for name in names)+r')(?!\w)')
    output = []
    last = 0
    for match in STRING.finditer(expression):
        output.append(pattern.sub(lambda found: mapping[found[0]], expression[last:match.start()]))
        output.append(match[0])
        last = match.end()
    output.append(pattern.sub(lambda found: mapping[found[0]], expression[last:]))
    return ''.join(output)


def known(kind, types):
    return kind in TYPES or kind in types or kind.endswith('[]') and kind[:-2] in types


def native(kind, types=None):
    types = types or {}
    if kind in TYPES:
        return TYPES[kind]
    if kind in types:
        return kind
    if kind.endswith('[]') and kind[:-2] in types:
        return 'Arr '+kind[:-2]+' _'
    raise BuildError(f'Unknown template type {kind!r}')


def fields(text, path, types):
    result = {}
    for item in text.split(','):
        if not item.strip():
            continue
        parts = [p.strip() for p in item.split(':')]
        if len(parts) != 2 or not NAME.fullmatch(parts[0]) or parts[0] in result or not known(parts[1], types):
            raise BuildError(f'{path}: invalid/duplicate prop {item!r}; {TYPE_HELP}')
        result[parts[0]] = parts[1]
    return result


def parse(name, source, path='<template>'):
    lines = source.split('\n')
    match = re.fullmatch(r'@props\((.*)\)\s*', lines[0])
    if len(lines) < 2 or not match:
        raise BuildError(f'{path}: first line must be @props(name: text, ...)')
    types = {}
    index = 1
    # Record declarations describe fields of a Neo record type the application
    # defines. Neo's checker verifies them when the generated code compiles.
    while index < len(lines) and lines[index].startswith('@type'):
        declaration = re.fullmatch(r'@type\s+([A-Za-z_]\w*)\((.*)\)\s*', lines[index])
        if not declaration:
            raise BuildError(f'{path}: use @type Name(field: text, ...)')
        type_name = declaration[1]
        if type_name in types or type_name in TYPES or not type_name[0].isupper():
            raise BuildError(f'{path}: @type {type_name} must be a new name starting with a capital letter')
        types[type_name] = fields(declaration[2], path, {})
        index += 1
    props = fields(match[1], path, types)
    template = Template(name, props, '\n'.join(lines[index:]), str(path), types)
    validate_html(template)
    return template


def resolve_kind(name, kinds, types):
    parts = name.split('.')
    kind = kinds.get(parts[0])
    for part in parts[1:]:
        record = types.get(kind)
        if record is None or part not in record:
            return None
        kind = record[part]
    return kind


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
    for item, index, source in re.findall(r'{%\s*for\s+(\w+)(?:\s*,\s*(\w+))?\s+in\s+([\w.]+)\s*%}', template.body):
        kind = resolve_kind(source, local_types, template.types) or ''
        if kind.endswith('[]'):
            local_types[item] = kind[:-2]
        if index:
            local_types[index] = 'int'

    def kind_of(name):
        return resolve_kind(name, local_types, template.types)

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
            refresh = tag == 'meta' and (dict(attrs).get('http-equiv') or '').strip().lower() == 'refresh'
            for key, value in attrs:
                if key.startswith('on') or key == 'srcdoc':
                    raise BuildError(f'{template.path}: executable HTML attributes are not supported')
                if value and EXECUTABLE_URL.match(value):
                    raise BuildError(f'{template.path}: executable URLs are not supported')
                if refresh and key == 'content' and value and re.search(r'url\s*=\s*[\'"]?\s*(javascript|vbscript|data):', value, re.I):
                    raise BuildError(f'{template.path}: executable URLs are not supported')
                if value and ('{{' in value or '{%' in value):
                    if key in ('style', 'srcset') or key.startswith('on'):
                        raise BuildError(f'{template.path}: dynamic {key} is unsupported')
                    if refresh and key == 'content':
                        raise BuildError(f'{template.path}: dynamic meta refresh is unsupported')
                    if key in URL_ATTRIBUTES:
                        names = re.findall(r'{{\s*([\w.]+)\s*}}', value)
                        if len(names) != 1 or not re.fullmatch(r'\s*{{\s*[\w.]+\s*}}\s*', value) or kind_of(names[0]) != 'url':
                            raise BuildError(f'{template.path}: dynamic {key} requires one url prop')
                    if '{%' in value or not re.search(r'(?:^|\s)'+re.escape(key)+r'\s*=\s*[\"\']', raw, re.I):
                        raise BuildError(f'{template.path}: interpolate only inside quoted attribute values')
                    for name in re.findall(r'{{\s*([\w.]+)\s*}}', value):
                        if kind_of(name) == 'html':
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
    types = template.types
    kinds = dict(template.props)
    locals_ = {}

    def resolve(name):
        if not PATH.fullmatch(name):
            raise BuildError(f'{template.path}: arbitrary expressions are not supported: {name!r}')
        parts = name.split('.')
        if parts[0] not in kinds:
            raise BuildError(f'{template.path}: unknown prop {parts[0]!r}')
        expression = locals_.get(parts[0], 'p_'+parts[0])
        kind = kinds[parts[0]]
        for part in parts[1:]:
            record = types.get(kind)
            if record is None or part not in record:
                raise BuildError(f'{template.path}: {name!r} has no field {part!r}')
            expression += '.'+part
            kind = record[part]
        return expression, kind

    def condition(text):
        match = re.fullmatch(r'(!)?\s*([A-Za-z_][\w.]*)\s*(?:(==|!=|<=|>=|<|>)\s*(-?\d{1,18}|"[^"\\]*"|[A-Za-z_][\w.]*))?', text)
        if not match:
            raise BuildError(f'{template.path}: unsupported condition {text!r}')
        negative, name, operator, operand = match.groups()
        value, kind = resolve(name)
        if operator is None:
            if kind == 'bool':
                test = value
            elif kind == 'text' or kind.endswith('[]'):
                test = 'len('+value+')>0'
            else:
                raise BuildError(f'{template.path}: if requires a bool, text or list value; compare other values explicitly')
            if not negative:
                return test
            return '!'+test if re.fullmatch(r'[\w.]+', test) else '!('+test+')'
        if negative:
            raise BuildError(f'{template.path}: use != instead of ! with a comparison')
        if re.fullmatch(r'-?\d+', operand):
            if kind != 'int':
                raise BuildError(f'{template.path}: {name} is not an int')
            other = '(0-'+operand[1:]+')' if operand.startswith('-') else operand
        elif operand.startswith('"'):
            if kind != 'text' or operator not in ('==', '!='):
                raise BuildError(f'{template.path}: compare text with == or !=')
            other = literal(operand[1:-1])
        else:
            other, other_kind = resolve(operand)
            if other_kind != kind or kind not in ('int', 'text') or kind == 'text' and operator not in ('==', '!='):
                raise BuildError(f'{template.path}: incompatible comparison {text!r}')
        if kind == 'text':
            test = 'str_equal('+value+','+other+')'
            return test if operator == '==' else '!'+test
        return '('+value+operator+other+')'

    def join(parts):
        return literal('') if not parts else parts[0] if len(parts) == 1 else 'concat('+','.join(parts)+')'

    def branch(text):
        test = condition(text)
        yes, end = sequence(True)
        no = literal('')
        if end == 'else':
            no, end = sequence(True)
        elif end and end.startswith('elif '):
            no, end = branch(end[5:].strip())
        if end != 'endif':
            raise BuildError(f'{template.path}: missing endif')
        return '(if '+test+' then '+yes+' else '+no+')', end

    def sequence(nested=False):
        nonlocal position
        parts = []
        while position < len(tokens):
            token = tokens[position]
            position += 1
            if token.startswith('{{'):
                value, kind = resolve(token[2:-2].strip())
                if kind.endswith('[]'):
                    raise BuildError(f'{template.path}: render list props with a for loop')
                if kind in types:
                    raise BuildError(f'{template.path}: render record fields, not the record')
                parts.append('html_escape('+value+')' if kind == 'text' else 'int_text('+value+')' if kind == 'int'
                             else '(if '+value+' then "true" else "false")' if kind == 'bool' else 'html_escape('+value+'.value)' if kind == 'url' else value+'.bytes')
            elif token.startswith('{%'):
                directive = token[2:-2].strip()
                if directive in ('else', 'endif', 'endfor', 'endcomponent') or directive.startswith('elif '):
                    if not nested:
                        raise BuildError(f'{template.path}: unexpected {directive}')
                    return join(parts), directive
                if directive.startswith('if '):
                    expression, _ = branch(directive[3:].strip())
                    parts.append(expression)
                elif directive.startswith('for '):
                    match = re.fullmatch(r'for ([A-Za-z_]\w*)(?:\s*,\s*([A-Za-z_]\w*))? in ([A-Za-z_][\w.]*)', directive)
                    if not match:
                        raise BuildError(f'{template.path}: use for item in list_prop, or for item, index in list_prop')
                    item, index, source = match.groups()
                    array, source_kind = resolve(source)
                    if not source_kind.endswith('[]') or item in kinds or index and (index in kinds or index == item):
                        raise BuildError(f'{template.path}: for requires a list and fresh item/index names')
                    captures = list(locals_.items())
                    helper = f'text_{template.name}_loop_{len(helpers)}'
                    helpers.append('')
                    slot = len(helpers)-1
                    kinds[item] = source_kind[:-2]
                    locals_[item] = 'item_'+item
                    if index:
                        kinds[index] = 'int'
                        locals_[index] = 'idx_'+index
                    body, end = sequence(True)
                    del kinds[item]
                    del locals_[item]
                    if index:
                        del kinds[index]
                        del locals_[index]
                    if end != 'endfor':
                        raise BuildError(f'{template.path}: missing endfor')
                    args = ','.join('p_'+n+': '+native(k, types) for n, k in template.props.items())
                    args += ''.join(','+name_+': '+native(kinds[captured], types) for captured, name_ in captures)
                    call = ','.join(['p_'+n for n in template.props]+[name_ for _, name_ in captures])
                    position_binding = f'let idx_{index}=at in ' if index else ''
                    # A balanced tree avoids quadratic string copying for long lists.
                    helpers[slot] = (f'fn {helper}({args},at:i64,count:i64) -> Arr u8 _ = '
                        f'if count<=0 then "" else if count==1 then check at>=0 && at<len({array}) in '
                        f'let item_{item}={array}[at] in {position_binding}{body} else let half=count/2 in '
                        f'concat({helper}({call},at,half),{helper}({call},at+half,count-half))\n')
                    parts.append(f'{helper}({call},0,len({array}))')
                elif directive.startswith('component '):
                    words = directive.split()
                    name = words[1] if len(words) > 1 else ''
                    if name not in components or components[name].props.get('content') != 'html':
                        raise BuildError(f'{template.path}: component blocks require a content:html slot')
                    bindings = {}
                    for word in words[2:]:
                        key, eq, value = word.partition('=')
                        if not eq or key in bindings or key == 'content':
                            raise BuildError(f'{template.path}: invalid component binding')
                        bindings[key] = resolve(value)
                    component = components[name]
                    if set(bindings) != (set(component.props)-{'content'}):
                        raise BuildError(f'{template.path}: missing component props')
                    for key, (_, kind) in bindings.items():
                        if kind != component.props[key]:
                            raise BuildError(f'{template.path}: incompatible component prop {key}')
                    content, end = sequence(True)
                    if end != 'endcomponent':
                        raise BuildError(f'{template.path}: missing endcomponent')
                    dependencies.append(name)
                    parts.append('view_'+name+'('+','.join('web_html('+content+')' if prop == 'content' else bindings[prop][0] for prop in component.props)+').bytes')
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
                        bindings[key] = resolve(value)
                    if set(bindings) != set(component.props):
                        raise BuildError(f'{template.path}: {name} requires props {list(component.props)}')
                    for key, (_, kind) in bindings.items():
                        if kind != component.props[key]:
                            raise BuildError(f'{template.path}: incompatible prop {key} for {name}')
                    dependencies.append(name)
                    parts.append('view_'+name+'('+','.join(bindings[p][0] for p in component.props)+').bytes')
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
    args = ','.join('p_'+name+': '+native(kind, types) for name, kind in template.props.items())
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
