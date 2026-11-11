#!/bin/sh
set -eu
./neokit test
python3 tests/freestanding.py --binary build/studio/server
