#!/bin/sh
# NeoKit's command line: builds cli/main.neo with a Neo toolchain on first
# use and runs the executable, caching it per CLI sources and toolchain.
#
# This file becomes ./neokit; the framework root is the directory it lives
# in, after symbolic links (so a bin/neokit link to it works). The
# toolchain is NEO_HOME, else the SDK's ../neo, else the directory of `neo`
# on PATH after symbolic links, as compiler_env() picks it. The executable is
# ${XDG_CACHE_HOME:-$HOME/.cache}/neokit/cli/KEY/neokit, where KEY hashes
# the names and contents of cli/*.neo and the toolchain: its path, its neo
# launcher and its MANIFEST.json (which has the checksum of every file in
# it), or for a source checkout without one, the listing of its compilers
# and its VERSION, stdlib and runtime; plus `uname -sm` when the toolchain
# carries compilers for several platforms. Compilers are never read, so a
# cache hit costs a few milliseconds.
#
# A miss builds into a directory of its own beside the executable and
# renames the result into place, so runs that start at once each end with a
# whole executable. The build's output goes to stderr; when it fails, the
# launcher exits with status 1. The executable runs with NEOKIT_ROOT set to
# the framework root and the launcher's arguments.
#
# Bump LAUNCHER_KEY when the way the CLI is built changes. Everything
# cli/main.neo imports must stay in cli/, which is all the key covers.
set -eu
LAUNCHER_KEY=neokit-cli-1
nl='
'

fail() {
  printf 'neokit: %s\n' "$1" >&2
  exit 1
}

has_compiler() {
  [ -f "$1/bin/neoc" ] || [ -f "$1/.neo-build/bin/neoc" ]
}

# Changes to the physical directory of the file $1 names, after symbolic
# links.
cd_to_file() {
  path=$1
  case $path in */*) ;; *) path=./$path ;; esac
  while [ -h "$path" ]; do
    link=$(readlink "$path") || return 1
    case $link in
      /*) path=$link ;;
      *) path=${path%/*}/$link ;;
    esac
  done
  path=${path%/*}
  CDPATH= cd -P -- "${path:-/}"
}

if command -v sha256sum >/dev/null 2>&1; then
  hash=sha256sum
elif command -v shasum >/dev/null 2>&1; then
  hash='shasum -a 256'
else
  hash=cksum
fi

# Prints the framework root, the toolchain and the key on lines of their
# own. Each process started costs about a millisecond, so one subshell runs
# this and a cache hit starts only it, cat and the hash (and uname, for a
# toolchain with compilers for several platforms). It is a function rather
# than the body of $(...) because bash 3.2, macOS's sh, misreads case and
# here-documents there.
locate() {
  if [ -n "${NEO_HOME:-}" ]; then
    neo_home=$NEO_HOME
    case $neo_home in
      '~') neo_home=${HOME:-} ;;
      '~/'*) neo_home=${HOME:-}/${neo_home#'~/'} ;;
    esac
    case $neo_home in /*) ;; *) neo_home=$PWD/$neo_home ;; esac
  fi
  case $script in
    */*) self=$script ;;
    *) self=$(command -v "$script") || fail "cannot find $script on PATH" ;;
  esac
  cd_to_file "$self" || fail "cannot find the directory of $self"
  root=$PWD
  if [ -z "${NEO_HOME:-}" ]; then
    neo_home=${root%/*}/neo
    if ! has_compiler "$neo_home" && launcher=$(command -v neo) && cd_to_file "$launcher" &&
      has_compiler "$PWD"; then
      neo_home=$PWD
    fi
  fi
  if CDPATH= cd -P -- "$neo_home" 2>/dev/null; then
    neo_home=$PWD
  fi
  if ! has_compiler "$neo_home" || [ ! -f "$neo_home/neo" ]; then
    fail "Set NEO_HOME to an installed native Neo toolchain; checked $neo_home"
  fi
  machine=
  for compiler in "$neo_home"/bin/neoc-*; do
    [ ! -e "$compiler" ] || machine=$(uname -sm)
    break
  done
  cd -- "$root/cli" || fail "cannot read the CLI sources in $root/cli"
  names=
  for source in *.neo; do
    names=$names$source/
  done
  printf '%s\n%s\n' "$root" "$neo_home"
  if [ -f "$neo_home/MANIFEST.json" ]; then
    cat - "$neo_home/neo" "$neo_home/MANIFEST.json" *.neo <<EOF | $hash
$LAUNCHER_KEY
$machine
$names
EOF
  else
    {
      printf '%s\n' "$LAUNCHER_KEY" "$machine" "$names"
      ls -lnLi -- "$neo_home"/bin/neoc* "$neo_home"/.neo-build/bin/neoc* 2>/dev/null || :
      cat -- "$neo_home/neo" "$neo_home/VERSION" "$neo_home"/stdlib/*.neo "$neo_home"/runtime/* *.neo 2>/dev/null || :
    } | $hash
  fi
}

script=$0
found=$(locate) || exit 1
key=${found##*"$nl"}
found=${found%"$nl"*}
neo_home=${found##*"$nl"}
root=${found%"$nl"*}
case $hash in
  cksum) key=${key%% *}-${key#* } ;;
  *) key=${key%% *} ;;
esac
[ -n "$key" ] || fail "cannot hash the CLI sources in $root/cli"
case ${XDG_CACHE_HOME:-} in
  '') [ -n "${HOME:-}" ] || fail 'set HOME or XDG_CACHE_HOME for the CLI cache'
      cache=$HOME/.cache ;;
  *) cache=$XDG_CACHE_HOME ;;
esac
cache=$cache/neokit/cli/$key
exe=$cache/neokit

if [ ! -x "$exe" ] || [ ! -s "$exe" ]; then
  mkdir -p -- "$cache" || fail "cannot create $cache"
  work=$(mktemp -d "$cache/build.XXXXXX") || fail "cannot create a build directory in $cache"
  trap 'rm -rf -- "$work"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  if [ -t 2 ]; then
    printf 'neokit: building the command line with %s (once per NeoKit and Neo version)\n' "$neo_home" >&2
  fi
  if ! (
    cd -- "$work"
    unset NEOC
    NEO_HOME=$neo_home
    NEO_ROOT=$neo_home
    export NEO_HOME NEO_ROOT
    exec "$neo_home/neo" build "$root/cli/main.neo" -o "$work/neokit"
  ) >&2; then
    fail "could not build $root/cli/main.neo with $neo_home"
  fi
  [ -x "$work/neokit" ] || fail "building $root/cli/main.neo gave no executable"
  mv -f -- "$work/neokit" "$exe" || fail "cannot move the CLI into $cache"
  rm -rf -- "$work"
  trap - EXIT INT TERM
fi

NEOKIT_ROOT=$root
export NEOKIT_ROOT
exec "$exe" "$@"
