#!/bin/sh
set -eu
./web test
python3 tests/freestanding.py --binary build/studio/server
