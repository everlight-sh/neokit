"""Regenerate the release fixture and its golden output from
compiler/release.py, the reference.

Run on the test box (Python 3.12), then compare with the native port
(cli/release.neo) through driver.neo:

    python3 tests/cli_golden/release/generate.py
    NEO_HOME=/root/neo-dev/neo-feat $NEO_HOME/neo test tests/cli_golden/release/release.neo.toml

The script writes fixture/ (a small framework root, the neo-libs package
beside it, a stand-in multi-platform archive of release.py's NEO_VERSION
whose `neo pkg verify` is a shell script, and the archives of
fixture/unpack/), the expected output golden/fixture.out and the manifest
release.neo.toml.

The expected output is what release.py gives on the fixture, plus the
documented differences of the native port, applied here after release.py's
assemble() and before bundle() archives the tree:

- the command layer is cli/, not compiler/;
- .ts files are sources too;
- VERSION (the version and a newline) is written before MANIFEST.json, so
  MANIFEST.json lists it;
- SHA256SUMS (`sha256sum -c` format) lists every file but itself.

The driver's `fixture` mode prints, for SOURCE_DATE_EPOCH=1700000000:
the bundle (gzip header, each tar member's mode, size, SHA-256 and name,
the SHA-256 of the whole tar), the assembled SDK's MANIFEST.json,
SHA256SUMS, VERSION and README.md, the error of a second assemble to the
same place, and unpack_neo of each fixture/unpack/ archive with its own
SHA-256 in upper case (the files it extracts, with modes), with a wrong
one and with none. Modes assume the umask 022.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from compiler import release  # noqa: E402
from compiler.templates import BuildError  # noqa: E402

FIXTURE = HERE/'fixture'
GOLDEN = HERE/'golden'
EPOCH = 1700000000
ARCHIVE_MTIME = 1690000000

VERSION = '9.8.7-fixture.1'
TOP = f'neo-{release.NEO_VERSION}'
NEO_LINE = f'{release.NEO_VERSION}\n'.encode()
LONG = ('examples/a-very-long-directory-name-for-the-ustar-prefix-field-0123456789/'
        'another-long-directory-name-abcdefghij/page.view')

# path below fixture/ -> (text, mode)
FILES = {
    'root/neokit': ('#!/bin/sh\necho fixture launcher\n', 0o755),
    'root/web': ('#!/bin/sh\necho fixture web\n', 0o755),
    'root/neo.toml': f'[package]\nname = "neokit"\nversion = "{VERSION}"\nentry = "src/lib.neo"\n\n[dependencies]\nsample={{path="../neo-libs/sample",version="1.0.0"}}\n',
    'root/neo.lock': json.dumps({
        'format': 1,
        'packages': {
            'neo-sample@1.0.0': {'dependencies': {}, 'dependency_paths': {}, 'name': 'neo-sample',
                                 'path': '../neo-libs/sample', 'sha256': '0'*64, 'version': '1.0.0'},
            f'neokit@{VERSION}': {'dependencies': {'sample': 'neo-sample@1.0.0'},
                                  'dependency_paths': {'sample': '../neo-libs/sample'},
                                  'name': 'neokit', 'path': '.', 'sha256': None, 'version': VERSION},
        },
        'root': f'neokit@{VERSION}',
    }, indent=2)+'\n',
    'root/package.json': '{"name": "neokit-fixture", "private": true}\n',
    'root/bun.lock': '{}\n',
    'root/Dockerfile': 'FROM scratch\n',
    'root/.dockerignore': 'build\n',
    'root/README.md': '# Fixture\n',
    'root/AGENTS.md': '# Agents\n',
    'root/LICENSE': 'Fixture license\n',
    'root/SECURITY.md': '# Security\n',
    'root/CHANGELOG.md': '# Changes\n',
    'root/cli/main.neo': 'fn main() -> i64 = 0\n',
    'root/cli/launcher.sh': ('#!/bin/sh\nexit 0\n', 0o755),
    'root/cli/notes.txt': 'not a source\n',
    'root/compiler/cli.py': 'print("the Python command layer")\n',
    'root/client/runtime.js': 'export function start() {}\n',
    'root/client/types.ts': 'export type T = number;\n',
    'root/src/lib.neo': 'fn lib() -> i64 = 1\n',
    'root/src/.hidden.neo': 'skipped\n',
    'root/src/.secret/inner.neo': 'skipped\n',
    'root/src/build/out.neo': 'skipped\n',
    'root/src/vendor/x.js': 'skipped\n',
    'root/examples/app/.env.example': 'KEY=value\n',
    'root/examples/app/.env': 'skipped\n',
    'root/examples/app/app/page.view': '<p>hi</p>\n',
    'root/examples/app/data/rows.json': '[]\n',
    'root/'+LONG: '<p>long</p>\n',
    'root/docs/alpha-release.md': '# Alpha\n\nSee ../SECURITY.md and ../SECURITY.md.\n',
    'root/deploy/install.sh': ('#!/bin/sh\necho fixture install\n', 0o755),
    'root/deploy/Caddyfile.local': ':80\n',
    'root/tools/image.bin': 'skipped\n',
    'root/tools/run.py': 'print(1)\n',
    'root/tests/LICENSE': 'kept by name\n',
    'root/tests/lib.ts': 'export {};\n',
    'neo-libs/sample/lib.neo': 'fn sample() -> i64 = 2\n',
    'neo-libs/sample/LICENSE': 'Sample license\n',
    'neo-libs/sample/LICENSE-THIRD-PARTY': 'Third party\n',
    'neo-libs/sample/notes.txt': 'skipped\n',
}

# `neo pkg verify PATH` succeeds; `neo pkg lock PATH` writes a stand-in
# neo.lock (tests/cli_golden/scaffold uses the SDK this fixture assembles);
# any other command fails with exit status 2.
FAKE_NEO = ('#!/bin/sh\n'
            'if [ "$#" -eq 3 ] && [ "$1" = pkg ] && [ "$2" = verify ]; then exit 0; fi\n'
            'if [ "$#" -eq 3 ] && [ "$1" = pkg ] && [ "$2" = lock ]; then printf \'{"locked": "%s"}\\n\' "${3##*/}" > "$3/neo.lock"; exit 0; fi\n'
            'echo "fixture neo: unexpected arguments: $*" >&2\nexit 2\n')


def tar_bytes(entries, format=tarfile.USTAR_FORMAT, compress=True):
    """entries: (name, kind, data or link, mode)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=format) as tar:
        for name, kind, data, mode in entries:
            info = tarfile.TarInfo(name)
            info.mtime = ARCHIVE_MTIME
            info.mode = mode
            if kind == 'dir':
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == 'symlink':
                info.type = tarfile.SYMTYPE
                info.linkname = data
                tar.addfile(info)
            elif kind == 'hardlink':
                info.type = tarfile.LNKTYPE
                info.linkname = data
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    if not compress:
        return raw.getvalue()
    out = io.BytesIO()
    with gzip.GzipFile(filename='', fileobj=out, mode='wb', mtime=0) as zipped:
        zipped.write(raw.getvalue())
    return out.getvalue()


def neo_entries():
    top = f'neo-{release.NEO_VERSION}'
    entries = [(top, 'dir', None, 0o755), (f'{top}/VERSION', 'file', f'{release.NEO_VERSION}\n'.encode(), 0o644),
               (f'{top}/neo', 'file', FAKE_NEO.encode(), 0o755), (f'{top}/bin', 'dir', None, 0o755)]
    for platform in release.NEO_BINARIES:
        entries.append((f'{top}/bin/neoc-{platform}', 'file', f'#!/bin/sh\necho neoc {platform}\n'.encode(), 0o755))
    return entries


UNPACK = {
    '01-good.tar.gz': lambda: tar_bytes(neo_entries()+[
        (TOP+'/share/readme', 'file', b'read me\n', 0o600),
        (TOP+'/lib/deep/x.txt', 'file', b'no directory entry before me\n', 0o4775),
        ('./'+TOP+'//bin/./dotted', 'file', b'dotted\n', 0o644),
        (TOP+'/empty', 'file', b'', 0o644),
    ]),
    '02-plain.tar': lambda: tar_bytes([(TOP+'/VERSION', 'file', NEO_LINE, 0o644)], compress=False),
    '03-dotdot.tar.gz': lambda: tar_bytes([(TOP+'/VERSION', 'file', NEO_LINE, 0o644),
                                           (TOP+'/../escape', 'file', b'out\n', 0o644)]),
    '04-absolute.tar.gz': lambda: tar_bytes([('/'+TOP+'/VERSION', 'file', NEO_LINE, 0o644)]),
    '05-other-top.tar.gz': lambda: tar_bytes([('neo-0.2.4/VERSION', 'file', b'0.2.4\n', 0o644)]),
    '06-symlink.tar.gz': lambda: tar_bytes([(TOP+'/VERSION', 'file', NEO_LINE, 0o644),
                                            (TOP+'/link', 'symlink', '../../outside', 0o777)]),
    '07-hardlink.tar.gz': lambda: tar_bytes([(TOP+'/VERSION', 'file', NEO_LINE, 0o644),
                                             (TOP+'/hard', 'hardlink', TOP+'/VERSION', 0o644)]),
    '08-gnu-long-name.tar.gz': lambda: tar_bytes([(TOP+'/'+'g'*120+'/file', 'file', b'gnu\n', 0o644)],
                                                 format=tarfile.GNU_FORMAT),
    '09-pax-long-name.tar.gz': lambda: tar_bytes([(TOP+'/'+'p'*120+'/file', 'file', b'pax\n', 0o755)],
                                                 format=tarfile.PAX_FORMAT),
    '10-top-only.tar.gz': lambda: tar_bytes([(TOP, 'dir', None, 0o755), (TOP+'/', 'dir', None, 0o755)]),
    '11-empty-name.tar.gz': lambda: tar_bytes([(TOP+'/VERSION', 'file', NEO_LINE, 0o644), ('.', 'file', b'x', 0o644)]),
}


def write_fixture():
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    for name, value in FILES.items():
        text, mode = value if isinstance(value, tuple) else (value, 0o644)
        path = FIXTURE/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
    (FIXTURE/f'{TOP}.tar.gz').write_bytes(tar_bytes(neo_entries()))
    (FIXTURE/'unpack').mkdir()
    for name, make in UNPACK.items():
        (FIXTURE/'unpack'/name).write_bytes(make())


# ---------------------------------------------------------------- reference

def native_assemble(original):
    """release.assemble with the native port's documented differences."""
    def assemble(destination, root, environment, neo_archive=None, neo_sha256=None):
        root = Path(root).resolve()
        suffixes = set(release.SOURCE_SUFFIXES)
        release.SOURCE_SUFFIXES.add('.ts')
        try:
            original(destination, root, environment, neo_archive, neo_sha256)
            destination = Path(destination).resolve()
            if (destination/'framework/compiler').exists():
                shutil.rmtree(destination/'framework/compiler')
            release.copy_sources(root/'cli', destination/'framework/cli')
        finally:
            release.SOURCE_SUFFIXES.clear()
            release.SOURCE_SUFFIXES.update(suffixes)
        manifest = json.loads((destination/'MANIFEST.json').read_text())
        (destination/'MANIFEST.json').unlink()
        (destination/'VERSION').write_text(manifest['version']+'\n')
        manifest['files'] = {path.relative_to(destination).as_posix(): release.sha256(path)
                             for path in sorted(destination.rglob('*')) if path.is_file()}
        (destination/'MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n')
        sums = ''.join(f'{release.sha256(path)}  {path.relative_to(destination).as_posix()}\n'
                       for path in sorted(destination.rglob('*')) if path.is_file())
        (destination/'SHA256SUMS').write_text(sums)
        return manifest
    return assemble


def shown(call):
    try:
        result = call()
    except BuildError as error:
        return f'error: {error}'
    return f'ok {result}'


def listing(tar):
    out = ''
    with tarfile.open(fileobj=io.BytesIO(tar)) as source:
        for member in source:
            data = source.extractfile(member).read()
            out += f'{member.mode} {member.size} {hashlib.sha256(data).hexdigest()} {member.name}\n'
    return out


def reference(base):
    archive = FIXTURE/f'{TOP}.tar.gz'
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    root = FIXTURE/'root'
    os.environ['SOURCE_DATE_EPOCH'] = str(EPOCH)
    original = release.assemble
    release.assemble = native_assemble(original)
    try:
        result = release.bundle(base/'out/neokit.tar.gz', root, archive, checksum)
        packed = Path(result['archive']).read_bytes()
        tar = gzip.decompress(packed)
        out = ('=== bundle\n'
               f'version {result["version"]}\n'
               f'gzip header {packed[:10].hex()}\n'
               f'sha256 matches file {"yes" if result["sha256"] == hashlib.sha256(packed).hexdigest() else "no"}\n'
               + listing(tar) +
               f'tar {hashlib.sha256(tar).hexdigest()} {len(tar)}\n')
        sdk = base/'sdk'
        release.assemble(sdk, root, os.environ, archive, checksum)
        again = shown(lambda: release.assemble(sdk, root, os.environ, archive, checksum))
    finally:
        release.assemble = original
    out += ('=== assemble\n'
            '--- MANIFEST.json\n' + (sdk/'MANIFEST.json').read_text() +
            '--- SHA256SUMS\n' + (sdk/'SHA256SUMS').read_text() +
            '--- VERSION\n' + (sdk/'VERSION').read_text() +
            '--- README.md\n' + (sdk/'README.md').read_text() +
            '--- again\n' + again.replace(str(base), 'BASE') + '\n')
    out += '=== unpack\n'
    for i, name in enumerate(sorted(os.listdir(FIXTURE/'unpack'))):
        path = FIXTURE/'unpack'/name
        target = base/f'unpack-{i}'
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()

        def unpack():
            release.unpack_neo(path, digest, target)
            with tarfile.open(path) as source:
                return len(source.getmembers())
        result = shown(unpack)
        out += f'{name}: {result.replace(str(base), "BASE")}\n'
        if result.startswith('ok'):
            for item in sorted(target.rglob('*')):
                relative = item.relative_to(target).as_posix()
                if item.is_dir():
                    out += f'  {relative}/\n'
                else:
                    out += f'  {relative} {item.stat().st_mode & 0o7777} {release.sha256(item)}\n'
        out += f'{name} wrong sum: {shown(lambda: release.unpack_neo(path, "00", base/"never"))}\n'
        out += f'{name} no sum: {shown(lambda: release.unpack_neo(path, "", base/"never"))}\n'
    return out


MANIFEST = '''# Generated by generate.py from compiler/release.py; do not edit.
# Run with the Neo 0.2.6 branch build: neo test tests/cli_golden/release/release.neo.toml

[[test]]
name = "release bundle, assemble and unpack_neo on the fixture"
file = "driver.neo"
args = ["fixture"]
stdout-file = "golden/fixture.out"
timeout = 120
'''


def main():
    os.umask(0o022)
    write_fixture()
    GOLDEN.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cli-golden-release-') as temporary:
        base = Path(temporary).resolve()
        (GOLDEN/'fixture.out').write_text(reference(base))
    (HERE/'release.neo.toml').write_text(MANIFEST)


if __name__ == '__main__':
    main()
