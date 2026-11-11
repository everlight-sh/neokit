#!/bin/sh
set -eu
if [ "$#" -gt 1 ]; then
  printf 'Usage: %s [prefix]\n' "$0" >&2
  exit 2
fi
sdk=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
prefix=${1:-$HOME/.local}
case "$prefix" in
  /*) ;;
  *) prefix=$PWD/$prefix ;;
esac
cd "$sdk/framework"
exec python3 -m compiler.release "$sdk" "$prefix"
