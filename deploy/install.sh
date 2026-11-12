#!/bin/sh
# Install an extracted NeoKit SDK into PREFIX (default: $HOME/.local):
# PREFIX/lib/neokit/VERSION holds the SDK and PREFIX/bin/neokit links to its
# launcher. Plain POSIX sh; it runs no NeoKit code.
#
# Every file listed in SHA256SUMS is copied into a staging directory beside
# the target, the staged copy is verified with `sha256sum -c` (or
# `shasum -a 256 -c`), and only then renamed into place. An existing
# installation of any version at PREFIX/bin/neokit, or of this version, is
# refused.
set -eu

fail() {
  printf 'neokit install: %s\n' "$1" >&2
  exit 1
}

if [ "$#" -gt 1 ]; then
  printf 'Usage: %s [prefix]\n' "$0" >&2
  exit 2
fi
sdk=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
prefix=${1:-$HOME/.local}
case $prefix in
  /*) ;;
  *) prefix=$PWD/$prefix ;;
esac
if [ -d "$prefix" ]; then
  prefix=$(CDPATH= cd -- "$prefix" && pwd -P)
fi

if command -v sha256sum >/dev/null 2>&1; then
  check_sums() { sha256sum -c SHA256SUMS; }
elif command -v shasum >/dev/null 2>&1; then
  check_sums() { shasum -a 256 -c SHA256SUMS; }
else
  fail 'sha256sum or shasum is required to verify the SDK'
fi

[ -f "$sdk/SHA256SUMS" ] || fail "SDK checksum or path mismatch: SHA256SUMS"
[ -f "$sdk/VERSION" ] && [ ! -h "$sdk/VERSION" ] || fail "SDK checksum or path mismatch: VERSION"
version=$(cat "$sdk/VERSION")
newline='
'
case $version in
  *"$newline"*|'') fail 'Invalid SDK manifest' ;;
esac
printf '%s\n' "$version" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?' || fail 'Invalid SDK manifest'

target=$prefix/lib/neokit/$version
link=$prefix/bin/neokit
if [ -e "$target" ] || [ -h "$target" ] || [ -e "$link" ] || [ -h "$link" ]; then
  fail "Installation already exists at $target or $link; choose an empty prefix"
fi
mkdir -p "$prefix/lib/neokit" "$prefix/bin"

# The staging directory beside the target, removed on any exit before the
# rename. mktemp is not POSIX but everywhere; mkdir is atomic as a fallback.
temporary=
cleanup() {
  if [ -n "$temporary" ]; then rm -rf "$temporary"; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
temporary=$(mktemp -d "$prefix/lib/neokit/.install-XXXXXXXX" 2>/dev/null) || {
  temporary=$prefix/lib/neokit/.install-$$
  mkdir "$temporary" || { temporary=; fail "Cannot create a staging directory in $prefix/lib/neokit"; }
}
stage=$temporary/sdk
mkdir "$stage"

# SHA256SUMS lines are "DIGEST  PATH" (or "DIGEST *PATH"). Paths must stay
# inside the SDK: no absolute paths, no "..", only regular files.
made=
while IFS= read -r line || [ -n "$line" ]; do
  digest=${line%%' '*}
  rest=${line#"$digest"}
  case $digest in
    *[!0-9a-f]*) fail "SDK checksum or path mismatch: $line" ;;
  esac
  case $rest in
    '  '?*|' *'?*) name=${rest#??} ;;
    *) fail "SDK checksum or path mismatch: $line" ;;
  esac
  [ "${#digest}" -eq 64 ] || fail "SDK checksum or path mismatch: $line"
  case $name in
    ''|/*|..|../*|*/..|*/../*|SHA256SUMS) fail "SDK checksum or path mismatch: $name" ;;
  esac
  if [ ! -f "$sdk/$name" ] || [ -h "$sdk/$name" ]; then
    fail "SDK checksum or path mismatch: $name"
  fi
  case $name in
    */*) directory=${name%/*} ;;
    *) directory= ;;
  esac
  if [ -n "$directory" ] && [ "$directory" != "$made" ]; then
    mkdir -p "$stage/$directory"
    made=$directory
  fi
  cp -p "$sdk/$name" "$stage/$name"
done < "$sdk/SHA256SUMS"
cp -p "$sdk/SHA256SUMS" "$stage/SHA256SUMS"

report=$temporary/check
if ! (cd "$stage" && check_sums) > "$report" 2>&1; then
  grep -v ': OK$' "$report" >&2 || :
  fail 'SDK checksum verification failed'
fi
[ "$(cat "$stage/VERSION")" = "$version" ] || fail 'SDK checksum or path mismatch: VERSION'
[ -f "$stage/framework/neokit" ] || fail 'SDK checksum or path mismatch: framework/neokit'

mv "$stage" "$target"
if ! ln -s "../lib/neokit/$version/framework/neokit" "$link"; then
  rm -rf "$target"
  fail "Cannot create $link"
fi
printf 'Installed NeoKit %s. Run %s doctor\n' "$version" "$link"
