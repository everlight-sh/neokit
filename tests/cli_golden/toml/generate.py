"""Regenerate the TOML goldens from Python's tomllib and json, the reference.

Run from anywhere on the test box:

    python3 tests/cli_golden/toml/generate.py

Inputs are every *.toml file in the repository (outside hidden, build,
node_modules and tests/cli_golden directories) and every file in cases/:
*.toml files are single documents and a .cases file holds several, each
after a line "#--- NAME" (the body includes its final newline). The script
first rewrites the byte-level fixtures below into cases/, then writes
golden/*.out and the manifest toml.neo.toml. Each manifest case runs
driver.neo (std:toml, so Neo 0.2.6) on some inputs and compares its output
byte for byte:

    NEO_HOME=/root/neo-dev/neo-feat $NEO_HOME/neo test tests/cli_golden/toml/toml.neo.toml

For each input the output is "=== LABEL", then json.dumps(tomllib.loads(
path.read_text()), indent=2) and a newline, exactly as compiler/build.py
reads the file, or "error" when tomllib rejects it. std:toml words its
errors differently, and reports an unterminated string or array on the line
where it starts where tomllib names the end of the document, so only the
rejection itself is compared.
"""
import json
import os
from pathlib import Path
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CASES = HERE/'cases'
GOLDEN = HERE/'golden'
SKIPPED = {'node_modules', 'build', '__pycache__'}

# Fixtures with bytes an editor would normalize: line endings, controls, a BOM.
BYTE_FIXTURES = {
    'crlf.toml': b'# CRLF endings, as read_text() converts them\r\n[site]\r\nname = "crlf"\r\n'
                 b'description = """multi\r\nline"""\r\nraw = \'\'\'a\r\nb\'\'\'\r\n',
    'lone-cr.toml': b'a = "x\ry"\n',
    'bom.toml': b'\xef\xbb\xbfa = 1\n',
    'no-final-newline.toml': b'[site]\nname = "end"',
    'eof-comment.toml': b'a = 1 # done',
    'eof-duplicate-key.toml': b'a = 1\na = 2',
    'eof-missing-value.toml': b'a =',
    'eof-key.toml': b'a',
    'eof-header.toml': b'[a',
    'eof-array-header.toml': b'[[a]',
    'eof-unterminated-basic.toml': b'a = "abc',
    'eof-unterminated-literal.toml': b"a = 'abc",
    'eof-unterminated-multiline.toml': b'a = """abc\n',
    'eof-unterminated-multiline-literal.toml': b"a = '''abc\n''",
    'eof-backslash.toml': b'a = "\\',
    'eof-short-escape.toml': b'a = "\\u12',
    'eof-after-escape.toml': b'a = "\\U0001F600',
    'eof-escaped-whitespace.toml': b'a = """x\\  ',
    'eof-unclosed-array.toml': b'a = [1,',
    'eof-unclosed-inline.toml': b'a = {b = 1',
    'eof-comment-control.toml': b'# \x01',
    'controls.cases': b''.join([
        b'#--- control in basic string\na = "x\x01y"\n',
        b'#--- delete in literal string\na = \'x\x7fy\'\n',
        b'#--- bell in comment\na = 1 # ring \x07\n',
        b'#--- nul in multiline literal\na = \'\'\'x\x00y\'\'\'\n',
        b'#--- escape in multiline basic\na = """x\x1by"""\n',
        b'#--- tab is fine\na = "x\ty" # tab\tcomment\nb = \'\t\'\n',
        b'#--- control in key\n"a\x02" = 1\n',
        b'#--- control after value\na = 1\x01\n',
    ]),
}


def repository_inputs():
    found = []
    for directory, folders, names in os.walk(ROOT):
        relative = Path(directory).relative_to(ROOT)
        folders[:] = sorted(name for name in folders if not name.startswith('.') and name not in SKIPPED
                            and (relative/name).as_posix() != 'tests/cli_golden')
        found += [(relative/name).as_posix() for name in names if name.endswith('.toml')]
    return sorted(found)


def result(text):
    try:
        return json.dumps(tomllib.loads(text), indent=2)+'\n'
    except tomllib.TOMLDecodeError:
        return 'error\n'


def chunks(text):
    found = []
    lines = text.split('\n')
    for index, line in enumerate(lines):
        last = index == len(lines)-1
        if not last and line.startswith('#--- '):
            found.append([line[5:], ''])
        elif found:
            found[-1][1] += line if last else line+'\n'
    return found


def output(inputs):
    parts = []
    for name in inputs:
        text = (ROOT/name).read_text(encoding='utf-8')
        if name.endswith('.cases'):
            parts += ['=== '+name+' '+label+'\n'+result(body) for label, body in chunks(text)]
        else:
            parts.append('=== '+name+'\n'+result(text))
    return ''.join(parts)


def case(name, inputs, golden):
    (GOLDEN/golden).write_text(output(inputs), encoding='utf-8', newline='\n')
    return ('[[test]]\nname = '+json.dumps(name)+'\nfile = "driver.neo"\nargs = ['+', '.join(json.dumps(i) for i in inputs)+']\n'
            'stdout-file = '+json.dumps('golden/'+golden)+'\n')


def main():
    CASES.mkdir(exist_ok=True)
    GOLDEN.mkdir(exist_ok=True)
    for name, data in BYTE_FIXTURES.items():
        (CASES/name).write_bytes(data)
    for stale in GOLDEN.glob('*.out'):
        stale.unlink()
    # Every case builds driver.neo once, so single documents share a case.
    files = sorted(path for path in CASES.iterdir() if path.is_file())
    single = [path.relative_to(ROOT).as_posix() for path in files if path.suffix == '.toml']
    entries = [case('toml repository files', repository_inputs(), 'repository.out'),
               case('toml cases/*.toml', single, 'documents.out')]
    for path in files:
        if path.suffix == '.cases':
            entries.append(case('toml cases/'+path.name, [path.relative_to(ROOT).as_posix()], path.stem+'.out'))
    header = ('# Generated by generate.py from Python\'s tomllib and json; do not edit.\n'
              '# Run with Neo 0.2.6 (std:toml): neo test tests/cli_golden/toml/toml.neo.toml\n\n')
    (HERE/'toml.neo.toml').write_text(header+'\n'.join(entries), encoding='utf-8', newline='\n')
    print(f'{len(entries)} cases, {sum(1 for _ in GOLDEN.glob("*.out"))} goldens')


if __name__ == '__main__':
    main()
