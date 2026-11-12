#!/bin/sh
set -eu
./neokit test
bun tests/tools/freestanding.ts --binary build/studio/server
