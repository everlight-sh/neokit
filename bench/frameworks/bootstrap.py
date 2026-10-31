#!/usr/bin/env python3
"""Prepare benchmark-only dependencies. Installation is outside measured builds."""
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent


def main():
    command=['bun','install']
    if (HERE/'bun.lock').is_file():command.append('--frozen-lockfile')
    subprocess.run(command,cwd=HERE,check=True)
    names=json.loads((HERE/'package.json').read_text())['dependencies']
    versions={name:json.loads((HERE/'node_modules'/name/'package.json').read_text())['version'] for name in names}
    (HERE/'installed-versions.json').write_text(json.dumps(versions,indent=2)+'\n')
    print(json.dumps(versions,indent=2))


if __name__=='__main__':main()
