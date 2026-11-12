"""Regenerate the argument-parsing golden from compiler/cli.py's argparse
parser, the reference.

Run on the test box (Python 3.12) from anywhere, then compare with the
native port (cli/args.neo) through driver.neo:

    python3 tests/cli_golden/args/generate.py
    NEO_HOME=/root/neo-dev/neo-feat $NEO_HOME/neo test tests/cli_golden/args/args.neo.toml

Each case runs `neokit ARGS` through compiler/cli.py's main() in a fresh
Python process with the given COLUMNS (unset when "-") and working
directory (the checkout, an application with a web.toml, or the default
application examples/studio itself), stopped at parse_args(): a successful
parse prints the namespace (sorted names; Path values as Path(...), strings
as repr) and exits 0; help and usage errors are argparse's own output and
exit status. The checkout's path is replaced by ROOT. The script writes the
cases (cases.tsv: COLUMNS, working directory and the arguments, separated
by tabs), the expected output golden/cases.out and the manifest args.neo.toml.

Python measures the terminal when COLUMNS is unset or invalid and stdout is
a terminal; the native parser then uses 80 columns. Here stdout is a pipe,
so both use 80.
"""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GOLDEN = HERE/'golden'
CWDS = {'root': ROOT, 'app': ROOT/'examples/blog', 'studio': ROOT/'examples/studio'}

HARNESS = r'''
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import compiler.cli as cli
original = argparse.ArgumentParser.parse_args
def shown(value):
    if isinstance(value, Path):
        return 'Path(%s)' % value
    if isinstance(value, str):
        return repr(value)
    return str(value)
def parse_args(self, args=None, namespace=None):
    parsed = original(self, args, namespace)
    for name in sorted(vars(parsed)):
        print('%s=%s' % (name, shown(getattr(parsed, name))))
    sys.exit(0)
argparse.ArgumentParser.parse_args = parse_args
sys.argv = ['neokit', *sys.argv[2:]]
cli.main()
'''

COMMANDS = ['prepare', 'build', 'serve', 'dev', 'export', 'test', 'init', 'release', 'doctor',
            'revalidate', 'password', 'migrate', 'preview']

# (COLUMNS or '-', working directory, arguments)
CASES = [('-', 'root', []), ('-', 'root', ['-h']), ('-', 'root', ['--help']), ('-', 'root', ['--he']),
         ('-', 'root', ['-h', 'build']), ('-', 'root', ['-hx']), ('-', 'root', ['-h=1']), ('-', 'root', ['--help=1']),
         ('-', 'root', ['--help='])]
CASES += [('-', 'root', [name, '-h']) for name in COMMANDS]
for columns in ('1', '20', '30', '40', '52', '60', '100', '200', 'abc', '0', ' 50 ', '-5'):
    for args in (['-h'], ['serve', '-h'], ['release', '-h'], ['init', '-h'], ['test', '-h'], ['export', '-h'],
                 ['prepare', '-h'], ['release'], ['nope']):
        CASES.append((columns, 'root', args))
for where in ('root', 'app', 'studio'):
    for name in COMMANDS:
        CASES.append(('-', where, [name] if name not in ('init', 'release') else
                      [name, 'x'] if name == 'init' else [name, '--out', 'o']))
CASES += [(('-', 'root', args)) for args in [
    ['nope'], ['help'], ['--', 'build'], ['--', '--'], ['nope-é'], ['-x'], ['-x', 'build'], ['--x', 'build', '--y'],
    ['build', '--port', '1'], ['build', '--strict'], ['build', '-h-x'], ['build', '-hx'], ['build', '--a'],
    ['build', '--app'], ['build', '--app', '--', 'x'], ['build', '--app=', 'x'], ['build', '--app', 'a b'],
    ['build', '--app', '-x y'], ['build', '--app', 'a//b/./c/', '--out', '//o', '--profile', 'dev'],
    ['build', '--out', 'é', '--app', '~/x'], ['build', '--', '--app', 'x'], ['build', '--app', 'a', '--app', 'b'],
    ['build', '--profile', 'x'], ['build', '--profile'], ['build', '--profile', '--app', 'x'], ['build', '--pro=dev'],
    ['build', '--profile=', 'x'], ['build', '-', 'x'],
    ['serve', '--port', 'abc'], ['serve', '--port='], ['serve', '--port', '-5'], ['serve', '--port', '1_000'],
    ['serve', '--port', ' 7 '], ['serve', '--port', '-1.5'], ['serve', '--port', '+3'], ['serve', '--port', '1__0'],
    ['serve', '--p', '1'], ['serve', '--po', '1'], ['serve', '--no'], ['serve', '--no-build=x'],
    ['serve', '--no-build', '--port', '9', '--app', 'x'], ['serve', '--p=1'], ['serve', '--=x'],
    ['dev', '--port', '70000'], ['dev', '--profile', 'release'],
    ['export', '--strict', '--dest', 'd'], ['export', '--d', 'x'], ['export', '--s'],
    ['test', '--extended', '--postgres'], ['test', '--e', '--p'], ['test', 'x'],
    ['init'], ['init', 'a', 'b'], ['init', '--', '-x'], ['init', '-x'], ['init', '--standalone=1'],
    ['init', '--standalone', 'p/q/'], ['init', 'x', '--'], ['init', '--', '--', 'x'], ['init', ''],
    ['release'], ['release', '--out', 'o', '--neo', 'x'], ['release', '--out', 'o', '--neo-s', 'abc'],
    ['release', '--out=o', '--neo-archive', 'a//b/'], ['release', '--out', 'o', '--neo-sha256', ''],
    ['release', '--neo-archive', 'a'],
    ['doctor', 'extra'], ['password', '--x'], ['prepare', '-h', '--bad'], ['prepare', '--bad', '-h'],
    ['revalidate', '--out', 'r'], ['migrate', '--build-only', '--app', 'm'], ['migrate', '--b'],
    ['preview', '--dir', 'x', '--port', '9'], ['preview', '--port', 'x'], ['preview', '--d=y'],
]]
CASES += [('-', 'app', ['preview']), ('-', 'app', ['revalidate']), ('-', 'studio', ['migrate']),
          ('-', 'app', ['export', '--app', 'elsewhere'])]


def run(columns, where, args):
    env = {key: value for key, value in os.environ.items() if key != 'COLUMNS'}
    if columns != '-':
        env['COLUMNS'] = columns
    result = subprocess.run([sys.executable, '-I', '-c', HARNESS, str(ROOT), *args], cwd=CWDS[where], env=env,
                            capture_output=True)
    shown = lambda data: data.decode().replace(str(ROOT), 'ROOT')
    return f'exit {result.returncode}\n--- stdout\n{shown(result.stdout)}--- stderr\n{shown(result.stderr)}'


MANIFEST = '''# Generated by generate.py from compiler/cli.py; do not edit.
# Run with the Neo 0.2.6 branch build: neo test tests/cli_golden/args/args.neo.toml

[[test]]
name = "argument parsing, help and usage errors"
file = "driver.neo"
args = ["tests/cli_golden/args/cases.tsv"]
stdout-file = "golden/cases.out"
timeout = 120
'''


def main():
    GOLDEN.mkdir(exist_ok=True)
    lines, out = [], []
    for columns, where, args in CASES:
        assert not any('\t' in arg or '\n' in arg for arg in args)
        lines.append('\t'.join([columns, where, *args]))
        out.append(f'=== {args!r} COLUMNS={columns} cwd={where}\n' + run(columns, where, args))
    (HERE/'cases.tsv').write_text('\n'.join(lines) + '\n')
    (GOLDEN/'cases.out').write_text(''.join(out))
    (HERE/'args.neo.toml').write_text(MANIFEST)


if __name__ == '__main__':
    main()
