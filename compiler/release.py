"""Assemble and install NeoKit's relocatable source SDK."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

from .templates import BuildError

NEO_VERSION = '0.2.6'
NEO_BINARIES = ('darwin-arm64', 'linux-arm64', 'linux-x86_64')

FRAMEWORK_FILES = (
    'neokit', 'web', 'neo.toml', 'neo.lock', 'package.json', 'bun.lock',
    'Dockerfile', '.dockerignore', 'README.md', 'AGENTS.md', 'LICENSE',
    'SECURITY.md', 'CHANGELOG.md',
)
SOURCE_SUFFIXES = {
    '.ax', '.neo', '.py', '.js', '.view', '.toml', '.css', '.svg',
    '.json', '.lock', '.md', '.sh', '.yaml', '.png', '.jpg', '.webp',
}
IGNORED = {'build', 'data', 'node_modules', 'vendor', '__pycache__', 'out'}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_sources(source, destination):
    for directory, folders, files in os.walk(source):
        folders[:] = sorted(name for name in folders if not name.startswith('.') and name not in IGNORED)
        for name in sorted(files):
            path = Path(directory)/name
            if name != '.env.example' and (name.startswith('.') or path.suffix not in SOURCE_SUFFIXES and name != 'LICENSE' and not name.startswith('Caddyfile')):
                continue
            if path.is_symlink():
                raise BuildError(f'Release source must be a regular file: {path}')
            target = destination/path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def unpack_neo(archive, checksum, destination):
    if not checksum or sha256(archive) != checksum.lower():
        raise BuildError('Neo archive SHA-256 does not match --neo-sha256')
    with tarfile.open(archive) as source:
        for member in source:
            relative = Path(member.name)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != f'neo-{NEO_VERSION}':
                raise BuildError(f'Unexpected compiler archive entry: {member.name}')
            target = destination/Path(*relative.parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(member).read())
                target.chmod(member.mode & 0o755)
            else:
                raise BuildError(f'Unexpected compiler archive entry type: {member.name}')


def assemble(destination, root, environment, neo_archive=None, neo_sha256=None):
    """Copy the verified framework, compiler and exact library graph."""
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise BuildError(f'SDK destination already exists: {destination}')
    with tempfile.TemporaryDirectory(prefix='neokit-sdk-') as temporary:
        stage = Path(temporary).resolve()/'sdk'
        framework = stage/'framework'
        framework.mkdir(parents=True)
        neo = stage/'neo'
        if neo_archive:
            unpack_neo(neo_archive, neo_sha256, neo)
        else:
            installed = root.parent/'neo'
            if not (installed/'bin/neoc-linux-arm64').is_file():
                raise BuildError('Use a released SDK for standalone scaffolding, or build a release with --neo-archive and --neo-sha256')
            shutil.copytree(installed, neo, ignore=shutil.ignore_patterns('.neo-build', '__pycache__'))
        if ((neo/'VERSION').read_text().strip() != NEO_VERSION or
                not all((neo/'bin'/f'neoc-{platform}').is_file() for platform in NEO_BINARIES)):
            raise BuildError(f'Expected a multi-platform native Neo {NEO_VERSION} bundle')
        bundled_env = {**environment, 'NEO_HOME':str(neo), 'NEO_ROOT':str(neo)}
        bundled_env.pop('NEOC', None)
        command = [str(neo/'neo'), 'pkg', 'verify']
        subprocess.run([*command, str(root)], env=bundled_env, check=True, stdout=subprocess.DEVNULL)
        for name in FRAMEWORK_FILES:
            shutil.copy2(root/name, framework/name)
        for name in ('compiler', 'client', 'src', 'examples', 'docs', 'deploy', 'tools', 'tests'):
            copy_sources(root/name, framework/name)
        lock = json.loads((root/'neo.lock').read_text())
        for identity, package in lock['packages'].items():
            if identity == lock['root']:
                continue
            relative = Path(package['path'])
            source = (root/relative).resolve()
            target = (framework/relative).resolve()
            if not target.is_relative_to(stage/'neo-libs'):
                raise BuildError(f'Unsupported release dependency location: {relative}')
            copy_sources(source, target)
            for notice in source.glob('LICENSE*'):
                if notice.is_file() and not notice.is_symlink():
                    shutil.copy2(notice, target/notice.name)
            if not (target/'LICENSE').is_file():
                raise BuildError(f'Missing release license for {identity}')
        subprocess.run([*command, str(framework)], env=bundled_env, check=True, stdout=subprocess.DEVNULL)
        shutil.copy2(framework/'deploy/install.sh', stage/'install.sh')
        guide = (framework/'docs/alpha-release.md').read_text()
        (stage/'README.md').write_text(guide.replace('../SECURITY.md', 'framework/SECURITY.md'))
        version = tomllib.loads((framework/'neo.toml').read_text())['package']['version']
        manifest = {
            'format': 1,
            'version': version,
            'neo_version': (stage/'neo/VERSION').read_text().strip(),
            'files': {path.relative_to(stage).as_posix():sha256(path)
                      for path in sorted(stage.rglob('*')) if path.is_file()},
        }
        (stage/'MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage, destination)
    return manifest


def bundle(output, root, neo_archive=None, neo_sha256=None):
    output = Path(output).resolve()
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '0'))
    if not 0 <= epoch <= 0xffffffff:
        raise BuildError('SOURCE_DATE_EPOCH must be between 0 and 4294967295')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.neokit-release-', dir=output.parent) as temporary:
        stage = Path(temporary)/'sdk'
        manifest = assemble(stage, root, os.environ, neo_archive, neo_sha256)
        archive = Path(temporary)/'release.tar.gz'
        with archive.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=epoch) as zipped:
            with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as tar:
                for path in sorted(stage.rglob('*')):
                    if not path.is_file():
                        continue
                    entry = tarfile.TarInfo(f'neokit-{manifest["version"]}/{path.relative_to(stage).as_posix()}')
                    entry.size = path.stat().st_size
                    entry.mode = 0o755 if path.stat().st_mode & 0o100 else 0o644
                    entry.mtime = epoch
                    with path.open('rb') as source:
                        tar.addfile(entry, source)
        os.replace(archive, output)
    return {'archive':str(output), 'version':manifest['version'], 'sha256':sha256(output)}


def install(source, prefix):
    source, prefix = Path(source).resolve(), Path(prefix).resolve()
    manifest = json.loads((source/'MANIFEST.json').read_text())
    version = manifest['version']
    if manifest['format'] != 1 or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise BuildError('Invalid SDK manifest')
    target = prefix/'lib/neokit'/version
    link = prefix/'bin/neokit'
    if os.path.lexists(target) or os.path.lexists(link):
        raise BuildError(f'Installation already exists at {target} or {link}; choose an empty prefix')
    target.parent.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.install-', dir=target.parent) as temporary:
        stage = Path(temporary)/'sdk'
        stage.mkdir()
        for name, checksum in manifest['files'].items():
            relative = Path(name)
            path = source/relative
            if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(source) or not path.is_file() or sha256(path) != checksum:
                raise BuildError(f'SDK checksum or path mismatch: {name}')
            output = stage/relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
        shutil.copy2(source/'MANIFEST.json', stage/'MANIFEST.json')
        os.rename(stage, target)
        try:
            link.symlink_to(os.path.relpath(target/'framework/neokit', link.parent))
        except OSError:
            shutil.rmtree(target)
            raise
    print(f'Installed NeoKit {version}. Run {link} doctor')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Install an extracted NeoKit SDK')
    parser.add_argument('source', type=Path)
    parser.add_argument('prefix', type=Path)
    args = parser.parse_args()
    try:
        install(args.source, args.prefix)
    except (BuildError, OSError, ValueError, KeyError) as error:
        parser.exit(1, f'neokit install: {error}\n')
