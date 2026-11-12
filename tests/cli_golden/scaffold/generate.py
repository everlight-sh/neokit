"""Regenerate the scaffold golden from compiler/scaffold.py, the reference.

Run on the test box (Python 3.12) after tests/cli_golden/release/generate.py
(whose fixture this uses), then compare with the native port
(cli/scaffold.neo) through driver.neo:

    python3 tests/cli_golden/scaffold/generate.py
    NEO_HOME=/root/neo-dev/neo-feat $NEO_HOME/neo test tests/cli_golden/scaffold/scaffold.neo.toml

The cases assemble an SDK from the release fixture (whose stand-in `neo pkg
lock` writes a one-line neo.lock), then run init() on it: a plain project,
a standalone one (which assembles a second SDK into vendor/neokit-sdk), an
existing empty directory, a nested path whose name needs JSON escaping, a
directory that is not empty, a file, a standalone project without a Neo
beside the framework, and a failing `neo pkg lock`. Each prints init's
result (the line `neokit init` prints, or the error) and the project tree:
every directory with its mode, every file with its mode, size and SHA-256,
and the text of the files outside vendor/. Nothing is excluded.

The SDKs differ from release.py's by the native port's documented changes
(see ../release/generate.py, whose native_assemble this applies). Modes
assume the umask 022.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from compiler import release, scaffold  # noqa: E402
from compiler.templates import BuildError  # noqa: E402

spec = importlib.util.spec_from_file_location('release_generate', HERE.parent/'release/generate.py')
release_generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_generate)

RELEASE = HERE.parent/'release/fixture'
GOLDEN = HERE/'golden'


def tree(top):
    out = b''
    for path in sorted(top.rglob('*')):
        relative = path.relative_to(top).as_posix().encode()
        if path.is_symlink():
            out += b'  ' + relative + b' -> link\n'
        elif path.is_dir():
            out += b'  ' + relative + b'/ ' + str(path.stat().st_mode & 0o7777).encode() + b'\n'
        else:
            data = path.read_bytes()
            out += b'  %s %d %d %s\n' % (relative, path.stat().st_mode & 0o7777, len(data), hashlib.sha256(data).hexdigest().encode())
            if not relative.startswith(b'vendor/'):
                out += b'    | ' + data.rstrip(b'\n').replace(b'\n', b'\n    | ') + b'\n'
    return out


def init_case(label, base, path, root, environment, standalone, listed):
    try:
        target = scaffold.init(path, root, environment, standalone=standalone)
        result = f'Created {target}. ' + ('Run ./neokit dev from the project directory.' if standalone else f'Run ./neokit dev --app {target}')
    except (BuildError, subprocess.CalledProcessError, OSError) as error:
        result = f'error: {error}'
    out = f'=== {label}\n{result.replace(str(base), "BASE")}\n'.encode()
    if listed and path.is_dir():
        out += tree(path).replace(str(base).encode(), b'BASE')
    return out


def reference(base):
    archive = RELEASE/f'neo-{release.NEO_VERSION}.tar.gz'
    environment = {**os.environ, 'SOURCE_DATE_EPOCH': '1700000000'}
    original = release.assemble
    release.assemble = release_generate.native_assemble(original)
    try:
        sdk = base/'sdk'
        try:
            release.assemble(sdk, RELEASE/'root', environment, archive, hashlib.sha256(archive.read_bytes()).hexdigest())
            assembled = 'ok'
        except BuildError as error:
            assembled = f'error: {error}'
        neo = str(sdk/'neo')
        bundled = {**environment, 'NEO_HOME': neo, 'NEO_ROOT': neo}
        framework = sdk/'framework'
        apps = base/'apps'
        (apps/'empty').mkdir(parents=True)
        (apps/'file').write_text('x')
        failing = base/'failing-neo'
        failing.mkdir()
        (failing/'neo').write_text('#!/bin/sh\nexit 3\n')
        (failing/'neo').chmod(0o755)
        failing_env = {**environment, 'NEO_HOME': str(failing), 'NEO_ROOT': str(failing)}
        return (f'=== sdk\n{assembled}\n'.encode()
                + init_case('plain', base, apps/'plain', framework, bundled, False, True)
                + init_case('standalone', base, apps/'standalone', framework, bundled, True, True)
                + init_case('existing empty directory', base, apps/'empty', framework, bundled, False, False)
                + init_case('nested and quoted name', base, apps/'deep/er/my "app" é', framework, bundled, False, True)
                + init_case('not empty', base, apps/'plain', framework, bundled, False, False)
                + init_case('a file', base, apps/'file', framework, bundled, False, False)
                + init_case('standalone without a Neo beside the root', base, apps/'no-neo', RELEASE/'root', bundled, True, True)
                + init_case('lock fails', base, apps/'failing', framework, failing_env, False, True))
    finally:
        release.assemble = original


MANIFEST = '''# Generated by generate.py from compiler/scaffold.py; do not edit.
# Run with the Neo 0.2.6 branch build: neo test tests/cli_golden/scaffold/scaffold.neo.toml

[[test]]
name = "scaffold init on the release fixture's SDK"
file = "driver.neo"
args = ["fixture"]
stdout-file = "golden/fixture.out"
timeout = 120
'''


def main():
    os.umask(0o022)
    GOLDEN.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cli-golden-scaffold-') as temporary:
        (GOLDEN/'fixture.out').write_bytes(reference(Path(temporary).resolve()))
    (HERE/'scaffold.neo.toml').write_text(MANIFEST)


if __name__ == '__main__':
    main()
