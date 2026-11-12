#!/usr/bin/env python3
"""Compare cli/release.neo and deploy/install.sh with compiler/release.py.

Run on the test box from the repository root, with NEO_HOME set to the
Neo 0.2.6 branch build:

    $NEO_HOME/neo build tests/cli_golden/release/driver.neo -o build/release-driver
    python3 tests/cli_golden/release/parity.py build/release-driver NEO_ARCHIVE

NEO_ARCHIVE is the multi-platform Neo release archive of release.py's
NEO_VERSION (neo-0.2.6.tar.gz: neo-0.2.6/bin/neoc-*), what `neokit release
--neo-archive` takes. Both implementations bundle the checkout with the
same archive and SOURCE_DATE_EPOCH, then:

1. the trees: every file the Python SDK has is in the native SDK with the
   same bytes and mode, except the documented differences (compiler/ is
   replaced by cli/, tests/ also has .ts files, VERSION and SHA256SUMS are
   new, and MANIFEST.json lists the files that differ);
2. MANIFEST.json: the native file is what release.py's manifest expression
   gives for the native tree;
3. the archive: the native tar (after gzip decompression) is byte for byte
   what release.py's bundle() writes for the native tree (its assemble()
   replaced by a copy of that tree), and the gzip headers match;
4. install.sh: installs the native SDK into a fresh prefix (the files of
   SHA256SUMS, modes kept, the bin/neokit link), refuses a second install,
   and refuses a tampered SDK without leaving anything behind; release.py's
   install() of the Python SDK installs the same layout.

Prints one line per check and exits 1 when one fails.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from compiler import release  # noqa: E402

EPOCH = '1700000000'
failures = []


def check(label, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + label + (f' ({detail})' if detail and not ok else ''))
    if not ok:
        failures.append(label)


def members(tar_bytes):
    """name -> (mode, data) of a tar's members, in order."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar:
            out[member.name] = (member.mode, tar.extractfile(member).read() if member.isfile() else None)
    return out


def strip_top(entries):
    return {name.split('/', 1)[1]: value for name, value in entries.items()}


def expected_difference(name):
    parts = name.split('/')
    return (name in ('VERSION', 'SHA256SUMS', 'MANIFEST.json')
            or parts[:2] in (['framework', 'cli'], ['framework', 'compiler'])
            or (parts[0] == 'framework' and name.endswith('.ts')))


def main():
    driver, archive = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    os.environ['SOURCE_DATE_EPOCH'] = EPOCH
    with tempfile.TemporaryDirectory(prefix='neokit-release-parity-') as temporary:
        work = Path(temporary)
        native_archive = work/'native/neokit.tar.gz'
        run = subprocess.run([str(driver), 'bundle', str(native_archive), str(ROOT), str(archive), checksum],
                             cwd=ROOT, capture_output=True, text=True)
        check('native bundle runs', run.returncode == 0, run.stdout + run.stderr)
        if run.returncode:
            return
        report = json.loads(run.stdout)
        native_gz = native_archive.read_bytes()
        check('native bundle reports the archive SHA-256', report['sha256'] == hashlib.sha256(native_gz).hexdigest())
        python_report = release.bundle(work/'python/neokit.tar.gz', ROOT, archive, checksum)
        python_gz = Path(python_report['archive']).read_bytes()
        check('bundle JSON keys and version', list(report) == list(python_report) and report['version'] == python_report['version'])
        check('gzip headers match (magic, flags, MTIME, XFL, OS)', native_gz[:10] == python_gz[:10],
              f'{native_gz[:10].hex()} != {python_gz[:10].hex()}')
        native_tar, python_tar = gzip.decompress(native_gz), gzip.decompress(python_gz)
        native, python = strip_top(members(native_tar)), strip_top(members(python_tar))

        # 1. trees
        missing = [name for name in python if not expected_difference(name) and name not in native]
        extra = [name for name in native if not expected_difference(name) and name not in python]
        differ = [name for name in python if name in native and not expected_difference(name) and native[name] != python[name]]
        check('trees match outside the documented differences', not missing and not extra and not differ,
              f'missing {missing[:5]}, extra {extra[:5]}, differ {differ[:5]}')
        cli = sorted(name for name in native if name.startswith('framework/cli/'))
        sources = sorted('framework/cli/'+path.name for path in (ROOT/'cli').glob('*.neo'))
        check('framework/cli has every cli/*.neo', [name for name in cli if name.endswith('.neo')] == sources, str(cli[:5]))
        check('framework/compiler is gone', not any(name.startswith('framework/compiler/') for name in native))
        ts = sorted(name for name in native if name.endswith('.ts'))
        expected_ts = sorted('framework/'+path.relative_to(ROOT).as_posix()
                             for folder in ('client', 'src', 'examples', 'docs', 'deploy', 'tools', 'tests')
                             for path in (ROOT/folder).rglob('*.ts')
                             if path.is_file() and not path.name.startswith('.')
                             and not any(part in release.IGNORED or part.startswith('.') for part in path.relative_to(ROOT).parts[:-1]))
        check('.ts sources are copied', ts == expected_ts, f'{len(ts)} vs {len(expected_ts)}')
        check('VERSION holds the version', native['VERSION'][1] == (report['version']+'\n').encode())

        # 2. manifest, recomputed by release.py's expression on the native tree
        stage = work/'native-stage'
        for name, (mode, data) in native.items():
            path = stage/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)
        # release.py hashes the stage before MANIFEST.json (and SHA256SUMS,
        # new) are written; VERSION is written first and so is listed.
        manifest_files = {path.relative_to(stage).as_posix(): release.sha256(path)
                          for path in sorted(stage.rglob('*'))
                          if path.is_file() and path.relative_to(stage).as_posix() not in ('MANIFEST.json', 'SHA256SUMS')}
        manifest = {'format': 1, 'version': report['version'], 'neo_version': (stage/'neo/VERSION').read_text().strip(),
                    'files': manifest_files}
        check('MANIFEST.json is release.py\'s manifest of the native tree',
              native['MANIFEST.json'][1] == (json.dumps(manifest, indent=2)+'\n').encode())
        sums = ''.join(f'{release.sha256(path)}  {path.relative_to(stage).as_posix()}\n'
                       for path in sorted(stage.rglob('*')) if path.is_file() and path.relative_to(stage).as_posix() != 'SHA256SUMS')
        check('SHA256SUMS lists every other file in sorted order', native['SHA256SUMS'][1] == sums.encode())
        verified = subprocess.run(['sha256sum', '-c', '--quiet', 'SHA256SUMS'], cwd=stage, capture_output=True, text=True)
        check('sha256sum -c accepts SHA256SUMS', verified.returncode == 0, verified.stdout + verified.stderr)

        # 3. tarfile's archive of the same tree
        def copied(destination, root, environment, neo_archive=None, neo_sha256=None):
            shutil.copytree(stage, destination)
            return json.loads((stage/'MANIFEST.json').read_text())
        original = release.assemble
        release.assemble = copied
        try:
            reference = release.bundle(work/'reference/neokit.tar.gz', ROOT, archive, checksum)
        finally:
            release.assemble = original
        reference_tar = gzip.decompress(Path(reference['archive']).read_bytes())
        same = reference_tar == native_tar
        at = next((i for i in range(min(len(reference_tar), len(native_tar))) if reference_tar[i] != native_tar[i]), None)
        check(f'uncompressed tar is byte-identical to tarfile\'s ({len(native_tar)} bytes, {len(native)} files)', same,
              f'sizes {len(native_tar)} vs {len(reference_tar)}, first difference at {at}')

        # 4. install.sh
        extracted = work/'download'
        extracted.mkdir()
        with tarfile.open(native_archive) as tar:
            for member in tar:
                path = extracted/member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(tar.extractfile(member).read())
                path.chmod(member.mode)
        sdk = extracted/f'neokit-{report["version"]}'
        prefix = work/'prefix with space'
        env = {'PATH': os.environ['PATH'], 'HOME': str(work), 'LC_ALL': 'C'}
        first = subprocess.run([str(sdk/'install.sh'), str(prefix)], env=env, capture_output=True, text=True)
        target = prefix/'lib/neokit'/report['version']
        link = prefix/'bin/neokit'
        check('install.sh installs', first.returncode == 0 and first.stdout == f'Installed NeoKit {report["version"]}. Run {link} doctor\n',
              first.stdout + first.stderr)
        installed = {path.relative_to(target).as_posix(): (path.stat().st_mode & 0o7777, path.read_bytes())
                     for path in target.rglob('*') if path.is_file()}
        check('the installation holds exactly the SDK files, modes kept', installed == native)
        check('bin/neokit links to the framework launcher', os.readlink(link) == f'../lib/neokit/{report["version"]}/framework/neokit'
              and link.resolve() == (target/'framework/neokit').resolve())
        check('no staging directory is left', sorted(p.name for p in (prefix/'lib/neokit').iterdir()) == [report['version']])
        again = subprocess.run([str(sdk/'install.sh'), str(prefix)], env=env, capture_output=True, text=True)
        check('a second install is refused', again.returncode == 1 and again.stderr ==
              f'neokit install: Installation already exists at {target} or {link}; choose an empty prefix\n', again.stderr)
        # Without sha256sum, install.sh verifies with `shasum -a 256 -c`.
        tools = work/'tools'
        tools.mkdir()
        for folder in os.environ['PATH'].split(os.pathsep):
            if os.path.isdir(folder):
                for name in os.listdir(folder):
                    if name != 'sha256sum' and not (tools/name).exists():
                        (tools/name).symlink_to(Path(folder)/name)
        if (tools/'shasum').exists():
            shasum = subprocess.run([str(sdk/'install.sh'), str(work/'shasum-prefix')], env={**env, 'PATH': str(tools)},
                                    capture_output=True, text=True)
            check('install.sh verifies with shasum when sha256sum is missing', shasum.returncode == 0
                  and (work/'shasum-prefix/bin/neokit').resolve() == (work/'shasum-prefix/lib/neokit'/report['version']/'framework/neokit').resolve(),
                  shasum.stdout + shasum.stderr)
        else:
            print('skip install.sh with shasum (no shasum on PATH)')
        tampered_prefix = work/'tampered'
        victim = sdk/'framework/cli/release.neo'
        victim.write_bytes(victim.read_bytes()+b'\n; tampered\n')
        bad = subprocess.run([str(sdk/'install.sh'), str(tampered_prefix)], env=env, capture_output=True, text=True)
        check('a tampered SDK is refused', bad.returncode == 1 and 'framework/cli/release.neo: FAILED' in bad.stderr
              and bad.stderr.endswith('neokit install: SDK checksum verification failed\n'), bad.stdout + bad.stderr)
        check('a refused install leaves no files', not (tampered_prefix/'bin/neokit').exists()
              and list((tampered_prefix/'lib/neokit').iterdir()) == [])
        victim.unlink()
        gone = subprocess.run([str(sdk/'install.sh'), str(work/'missing')], env=env, capture_output=True, text=True)
        check('a missing SDK file is refused', gone.returncode == 1 and 'neokit install: SDK checksum or path mismatch: framework/cli/release.neo' in gone.stderr,
              gone.stderr)
        usage = subprocess.run([str(sdk/'install.sh'), 'a', 'b'], env=env, capture_output=True, text=True)
        check('two arguments are a usage error', usage.returncode == 2 and usage.stderr.startswith('Usage: '))

        # release.py's install() of the Python SDK: the same layout.
        python_sdk = work/'python-sdk'
        with tarfile.open(work/'python/neokit.tar.gz') as tar:
            for member in tar:
                path = python_sdk/member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(tar.extractfile(member).read())
                path.chmod(member.mode)
        python_prefix = work/'python-prefix'
        release.install(python_sdk/f'neokit-{report["version"]}', python_prefix)
        python_target = python_prefix/'lib/neokit'/report['version']
        python_installed = {path.relative_to(python_target).as_posix(): (path.stat().st_mode & 0o7777, path.read_bytes())
                            for path in python_target.rglob('*') if path.is_file()}
        check('release.py installs the Python SDK the same way (outside the documented differences)',
              {k: v for k, v in python_installed.items() if not expected_difference(k)} ==
              {k: v for k, v in installed.items() if not expected_difference(k)}
              and os.readlink(python_prefix/'bin/neokit') == os.readlink(link))
        directory_modes = sorted({oct(path.stat().st_mode & 0o777) for path in target.rglob('*') if path.is_dir()})
        python_modes = sorted({oct(path.stat().st_mode & 0o777) for path in python_target.rglob('*') if path.is_dir()})
        check('installed directory modes match release.py\'s', directory_modes == python_modes, f'{directory_modes} vs {python_modes}')
    if failures:
        sys.exit(1)


if __name__ == '__main__':
    main()
