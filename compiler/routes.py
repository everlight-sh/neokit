"""Build-time route validation, precedence and export-path matching."""
import re
from .templates import BuildError

SEGMENT = re.compile(r'[A-Za-z0-9_.-]+|\[([A-Za-z_][A-Za-z0-9_]*)\]|\[\.\.\.([A-Za-z_][A-Za-z0-9_]*)\]|\[\[\.\.\.([A-Za-z_][A-Za-z0-9_]*)\]\]')
# (group) folders organise files and share layouts without appearing in URLs.
GROUP = re.compile(r'\([A-Za-z0-9_-]+\)')
CANONICAL = re.compile(r'/[A-Za-z0-9_./-]*')
METHODS = {'GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'}


def canonical_path(path):
    return (isinstance(path, str) and bool(CANONICAL.fullmatch(path)) and '//' not in path and
            (path == '/' or not path.endswith('/')) and not any(part in ('.', '..') for part in path.split('/')))


def route_path(folder, base):
    parts = [part for part in folder.relative_to(base).parts if not GROUP.fullmatch(part)]
    names = []
    for i, part in enumerate(parts):
        match = SEGMENT.fullmatch(part)
        if not match or part in ('.', '..'):
            raise BuildError(f'Unsupported route segment: {part}')
        names.extend(name for name in match.groups() if name)
        if part.startswith(('[...', '[[...')) and i != len(parts)-1:
            raise BuildError('Catch-all parameters must be the final route segment')
    if len(names) != len(set(names)):
        raise BuildError('Route parameter names must be unique')
    return '/'+'/'.join(parts)

def matches(pattern, path):
    if not canonical_path(path):
        return False
    parts = []
    for part in pattern.strip('/').split('/'):
        if part.startswith('[[...'): parts.append(r'(?:/[^/]+)*')
        elif part.startswith('[...'): parts.append(r'(?:/[^/]+)+')
        elif part.startswith('['): parts.append('/[^/]+')
        elif part: parts.append('/'+re.escape(part))
    return bool(re.fullmatch(''.join(parts) or '/',path))

def precedence(path):
    return tuple(3 if p.startswith('[[...') else 2 if p.startswith('[...') else 1 if p.startswith('[') else 0 for p in path.split('/')),path

def shape(path):
    return re.sub(r'\[\[\.\.\.[^]]+\]\]', '[[...]]', re.sub(r'\[\.\.\.[^]]+\]', '[...]', re.sub(r'\[(?!\[|\.\.\.)[^]]+\]', '[]', path)))

def methods(config, path):
    value = config.get('methods', ['GET'])
    if not isinstance(value,list) or not value or any(v not in METHODS for v in value) or len(set(value))!=len(value):
        raise BuildError(f'{path}: methods must be a nonempty list of distinct HTTP methods')
    if 'GET' in value and 'HEAD' not in value: value = [*value,'HEAD']
    return value
