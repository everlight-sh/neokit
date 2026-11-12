#!/usr/bin/env python3
"""Compare cli/scaffold.neo's init with compiler/scaffold.py's on this
checkout, with the real `neo pkg lock` (NEO_HOME).

    $NEO_HOME/neo build tests/cli_golden/scaffold/driver.neo -o build/scaffold-driver
    python3 tests/cli_golden/scaffold/parity.py build/scaffold-driver

Both create PLACE/app (at the same depth, so the relative paths agree) and
must print the same line and leave the same tree: every path, directory and
file mode and file content, neo.lock included. Nothing is excluded.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from compiler.build import compiler_env  # noqa: E402
sys.path.insert(0, str(HERE))
from generate import tree  # noqa: E402


PYTHON_INIT = (
    "import sys\nfrom pathlib import Path\n"
    "from compiler.scaffold import init\n"
    "from compiler.build import compiler_env\n"
    "target = init(sys.argv[1], Path(sys.argv[2]), compiler_env())\n"
    "print(f'Created {target}. Run ./neokit dev --app {target}')\n")


def paths(listing):
    return sum(1 for line in listing.splitlines() if not line.startswith(b"    |"))


def main():
    driver = Path(sys.argv[1]).resolve()
    environment = compiler_env()
    failed = False
    with tempfile.TemporaryDirectory(prefix='neokit-scaffold-parity-') as temporary:
        base = Path(temporary).resolve()
        # Each side's stdout holds `neo pkg lock`'s line (the child shares
        # it) and then the line `neokit init` prints.
        started = time.monotonic()
        python = subprocess.run([sys.executable, '-c', PYTHON_INIT, str(base/'python/app'), str(ROOT)], env=environment,
                                cwd=ROOT, capture_output=True)
        python_line = python.stdout.decode()
        python_time = time.monotonic() - started
        started = time.monotonic()
        run = subprocess.run([str(driver), 'init', str(base/'native/app'), str(ROOT), '0'], env=environment,
                             cwd=ROOT, capture_output=True)
        native_time = time.monotonic() - started
        native_line = run.stdout.decode()
        same_line = native_line == python_line.replace('/python/', '/native/')
        python_tree, native_tree = tree(base/'python/app'), tree(base/'native/app')
        print(('ok   ' if same_line else 'FAIL ') + 'init prints the same lines (neo pkg lock\'s, then its own)')
        print(('ok   ' if python_tree == native_tree else 'FAIL ')
              + f'init writes the same tree ({paths(python_tree)} paths, neo.lock included)')
        print(f'     python {python_time:.2f}s, native {native_time:.2f}s')
        if not same_line or python_tree != native_tree or run.returncode:
            failed = True
            sys.stdout.write(python.stderr.decode() + native_line + run.stderr.decode())
            for a, b in zip(python_tree.splitlines(), native_tree.splitlines()):
                if a != b:
                    print('python:', a.decode(errors='replace'))
                    print('native:', b.decode(errors='replace'))
                    break
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
