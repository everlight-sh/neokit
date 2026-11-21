#!/bin/sh
# Checks of the `neokit` launcher (the sh script at the repository root) in
# a temporary framework copy under build/launcher-test, mostly with
# tests/tools/launcher/stub_main.neo as cli/main.neo, which prints
# NEOKIT_ROOT and its arguments. Run it on Linux from the repository root,
# with NEO_HOME set to an installed native Neo toolchain:
#
#   sh tests/tools/launcher_test.sh [SHELL [full]]
#
# SHELL (default /bin/sh) is the program that runs the launcher: try
# /bin/dash and /bin/bash (for `bash --posix` or busybox sh, a script that
# execs them with its arguments). "full" also builds the real cli/main.neo
# through the launcher and times its cache hit. NEO_ARCHIVE, when set, names
# Neo's multi-platform release archive (neo-0.2.13.tar.gz) for the SDK
# checks. The toolchain is copied once (about its size in
# build/launcher-test, which is left for inspection). Prints one line per
# check; exits 1 when one fails.
set -u
REPO=$(pwd -P)
[ -f "$REPO/neokit" ] && [ -d "$REPO/cli" ] || { echo "run from the repository root" >&2; exit 2; }
[ -n "${NEO_HOME:-}" ] && [ -x "$NEO_HOME/neo" ] || { echo "set NEO_HOME to a native Neo toolchain" >&2; exit 2; }
STUBS=$REPO/tests/tools/launcher
RUN=${1:-/bin/sh}
FULL=${2:-}
TOOLCHAIN=$(cd "$NEO_HOME" && pwd -P)
ARCHIVE=${NEO_ARCHIVE:-}
T=$REPO/build/launcher-test
FW=$T/sdk/framework
FAILED=0
BASE_PATH=/usr/local/bin:/usr/bin:/bin
n=100
nl='
'

ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/       /'; FAILED=1; }
now() { date +%s%N; }
keys() { ls "$XDG_CACHE_HOME/neokit/cli" 2>/dev/null | wc -l | tr -d ' '; }
exes() { ls "$XDG_CACHE_HOME"/neokit/cli/*/neokit 2>/dev/null | wc -l | tr -d ' '; }
leftovers() { find "$XDG_CACHE_HOME" -name 'build.*' 2>/dev/null | wc -l | tr -d ' '; }
builds() { wc -l < "$T/calls" | tr -d ' '; }
launch() { "$RUN" "$FW/neokit" "$@"; }
expect() { printf 'root=%s\n' "$FW"; for a in "$@"; do printf 'arg=[%s]\n' "$a"; done; }
# A toolchain directory whose neo logs its name to $T/calls and runs $2's
# (with NEO_ROOT set to $2: neoc only trusts a runtime under NEO_ROOT).
wrapper() {
  mkdir -p "$1"
  for entry in "$2"/*; do ln -s "$entry" "$1/"; done
  rm "$1/neo"
  printf '#!/bin/sh\necho %s >> %s\nNEO_ROOT=%s exec %s/neo "$@"\n' "${1##*/}" "$T/calls" "$2" "$2" > "$1/neo"
  chmod 755 "$1/neo"
}
calls() { tail -n 1 "$T/calls" 2>/dev/null; }

rm -rf "$T"
mkdir -p "$FW" "$T/bin" "$T/work" "$T/links" "$T/pathbin"
: > "$T/calls"
cp -a "$REPO/cli" "$FW/cli"
cp "$REPO/neo.toml" "$REPO/neo.lock" "$FW/"
cp "$REPO/neokit" "$FW/neokit"
chmod 755 "$FW/neokit"
cp "$REPO/web" "$FW/web"
cp "$STUBS/stub_main.neo" "$FW/cli/main.neo"
wrapper "$T/tcA" "$TOOLCHAIN"
wrapper "$T/tcB" "$TOOLCHAIN"
wrapper "$T/tcD" "$TOOLCHAIN"
cp -a "$TOOLCHAIN" "$T/tcC"
cd "$T/work"
export XDG_CACHE_HOME="$T/cache"
export NEO_HOME="$T/tcA"
echo "shell: $RUN"

# ---- first build, arguments, NEOKIT_ROOT, exit status
started=$(now)
out=$(launch 'a b' '' '*' '-x' 2>"$T/err"); st=$?
took=$(( ($(now)-started)/1000000 ))
if [ "$st" = 0 ] && [ "$out" = "$(expect 'a b' '' '*' '-x')" ] && [ "$(keys)" = 1 ] && [ "$(calls)" = tcA ]; then
  ok "first run builds (${took} ms) and runs with NEOKIT_ROOT and the arguments"
else bad "first run" "status $st; out: $out; err: $(cat "$T/err")"; fi
[ ! -s "$T/err" ] && ok "a build with stderr not a terminal prints nothing" || bad "build output" "$(cat "$T/err")"
[ "$(leftovers)" = 0 ] && ok "no build directory left" || bad "leftover build directory"
exe=$(ls -d "$XDG_CACHE_HOME"/neokit/cli/*/neokit) || exit 1
out=$(launch fail x); st=$?
[ "$st" = 3 ] && [ "$out" = "$(expect fail x)" ] && ok "the CLI's exit status passes through" || bad "exit status" "status $st: $out"
out=$(NEOKIT_ROOT=/elsewhere launch r); [ "$out" = "$(expect r)" ] && ok "NEOKIT_ROOT from the environment is replaced" || bad "NEOKIT_ROOT" "$out"
out=$(env NEO_HOME="$T/tcA/" "$RUN" "$FW/neokit" y); [ "$(keys)" = 1 ] && ok "NEO_HOME with a trailing slash is the same toolchain" || bad "trailing slash rebuilt"
cmp -s "$REPO/neokit" "$REPO/web" && ok "./web is a copy of ./neokit" || bad "./web differs from ./neokit"
out=$("$RUN" "$FW/web" w); [ "$out" = "$(expect w)" ] && [ "$(keys)" = 1 ] && ok "./web runs the same executable" || bad "web" "$out"

# ---- cache hit cost
started=$(now); i=0; while [ $i -lt $n ]; do launch x >/dev/null; i=$((i+1)); done; launched=$(( ($(now)-started)/n/1000 ))
started=$(now); i=0; while [ $i -lt $n ]; do NEOKIT_ROOT=$FW "$exe" x >/dev/null; i=$((i+1)); done; direct=$(( ($(now)-started)/n/1000 ))
overhead=$((launched-direct))
[ "$overhead" -lt 10000 ] && ok "cache hit: ${launched} us per run, ${direct} us running the executable directly (${overhead} us launcher)" \
  || bad "cache hit too slow" "${launched} us vs ${direct} us"
[ "$(builds)" = 1 ] && ok "cache hits never run neo" || bad "neo ran on a hit" "$(cat "$T/calls")"

# ---- the key follows the sources
cp "$FW/cli/text.neo" "$T/text.neo.orig"
printf '\n; an edit\n' >> "$FW/cli/text.neo"
out=$(launch e); [ "$out" = "$(expect e)" ] && [ "$(keys)" = 2 ] && ok "editing a cli/*.neo file rebuilds" || bad "source edit" "keys $(keys)"
cp "$T/text.neo.orig" "$FW/cli/text.neo"
before=$(builds); out=$(launch e)
[ "$(builds)" = "$before" ] && ok "undoing it finds the first executable again" || bad "undo rebuilt"
touch "$FW/cli/host.neo"; out=$(launch e)
[ "$(builds)" = "$before" ] && ok "touching a source without changing it does not rebuild" || bad "touch rebuilt"
printf '; new\n' > "$FW/cli/zz_new.neo"; out=$(launch e)
[ "$(builds)" = "$((before+1))" ] && ok "adding a cli/*.neo file rebuilds" || bad "new file"
rm "$FW/cli/zz_new.neo"
before=$(builds); printf 'notes\n' >> "$FW/cli/README.md"; out=$(launch e)
[ "$(builds)" = "$before" ] && ok "other files in cli/ are not part of the key" || bad "README.md rebuilt"

# ---- the key follows the toolchain
before=$(builds)
out=$(env NEO_HOME="$T/tcB" "$RUN" "$FW/neokit" t)
[ "$out" = "$(expect t)" ] && [ "$(builds)" = $((before+1)) ] && [ "$(calls)" = tcB ] && ok "another toolchain builds its own executable" || bad "toolchain switch"
export NEO_HOME="$T/tcC"
out=$(launch c); count=$(keys)
if [ -f "$T/tcC/MANIFEST.json" ]; then
  printf ' ' >> "$T/tcC/MANIFEST.json"; out=$(launch c)
  [ "$(keys)" = $((count+1)) ] && ok "a changed MANIFEST.json rebuilds" || bad "MANIFEST change"
  printf '#\n' >> "$T/tcC/stdlib/io.neo"; out=$(launch c)
  [ "$(keys)" = $((count+1)) ] && ok "with MANIFEST.json, the stdlib files are not read" || bad "stdlib read despite MANIFEST"
  rm "$T/tcC/MANIFEST.json"
fi
out=$(launch c); count=$(keys)
[ "$out" = "$(expect c)" ] && ok "a toolchain without MANIFEST.json builds" || bad "no MANIFEST" "$out"
printf '#\n' >> "$T/tcC/stdlib/io.neo"; out=$(launch c)
[ "$(keys)" = $((count+1)) ] && ok "... and its stdlib changing rebuilds" || bad "stdlib change"
printf '\n' >> "$T/tcC/runtime/$(ls "$T/tcC/runtime" | head -n 1)"; out=$(launch c)
[ "$(keys)" = $((count+2)) ] && ok "... its runtime changing rebuilds" || bad "runtime change"
cp -p "$T/tcC/bin/neoc" "$T/neoc.new" && mv "$T/neoc.new" "$T/tcC/bin/neoc"; out=$(launch c)
[ "$(keys)" = $((count+3)) ] && ok "... a replaced compiler rebuilds" || bad "neoc replaced"
before=$(keys); out=$(launch c)
[ "$(keys)" = "$before" ] && ok "... and an unchanged one does not" || bad "unchanged toolchain rebuilt"
export NEO_HOME="$T/tcA"

# ---- failures
cp "$FW/cli/main.neo" "$T/main.neo.orig"
printf 'fn broken( -> \n' >> "$FW/cli/main.neo"
count=$(exes)
out=$(launch b 2>"$T/err"); st=$?
if [ "$st" = 1 ] && [ -z "$out" ] && grep -q 'main.neo' "$T/err" && grep -q "^neokit: could not build $FW/cli/main.neo with $T/tcA\$" "$T/err" &&
   [ "$(exes)" = "$count" ] && [ "$(leftovers)" = 0 ]; then
  ok "a failed build shows Neo's error, exits 1 and leaves nothing behind"
else bad "failed build" "status $st; out: $out; err: $(cat "$T/err")"; fi
sed -n '1,8p' "$T/err" | sed 's/^/       | /'
cp "$T/main.neo.orig" "$FW/cli/main.neo"
out=$(env XDG_CACHE_HOME=/proc/neokit-cache "$RUN" "$FW/neokit" u 2>"$T/err"); st=$?
[ "$st" = 1 ] && grep -q '^neokit: cannot create /proc/neokit-cache/neokit/cli/' "$T/err" && ok "an unwritable cache is an error" || bad "unwritable cache" "$st $(cat "$T/err")"
out=$(env -u XDG_CACHE_HOME -u HOME "$RUN" "$FW/neokit" h 2>"$T/err"); st=$?
[ "$st" = 1 ] && [ "$(cat "$T/err")" = 'neokit: set HOME or XDG_CACHE_HOME for the CLI cache' ] && ok "no HOME nor XDG_CACHE_HOME is an error" || bad "no HOME" "$st $(cat "$T/err")"
mkdir -p "$T/home"
out=$(env -u XDG_CACHE_HOME HOME="$T/home" "$RUN" "$FW/neokit" h); st=$?
[ "$st" = 0 ] && [ "$out" = "$(expect h)" ] && [ -x "$(ls -d "$T"/home/.cache/neokit/cli/*/neokit)" ] && ok "the cache defaults to \$HOME/.cache" || bad "HOME cache"

# ---- runs at the same time
export XDG_CACHE_HOME="$T/cache-concurrent"
before=$(builds)
for i in 1 2 3 4; do (launch "run $i" > "$T/out$i" 2>"$T/err$i"; echo $? > "$T/st$i") & done
wait
good=1
for i in 1 2 3 4; do
  [ "$(cat "$T/st$i")" = 0 ] && [ "$(cat "$T/out$i")" = "$(expect "run $i")" ] && [ ! -s "$T/err$i" ] || good=0
done
[ "$good" = 1 ] && [ "$(keys)" = 1 ] && [ "$(leftovers)" = 0 ] && [ "$(ls "$XDG_CACHE_HOME"/neokit/cli/*/)" = neokit ] &&
  ok "four first runs at once all succeed and leave one executable ($(( $(builds)-before )) builds)" || bad "concurrent" "$(cat "$T"/st? "$T"/err?)"

# ---- stopped mid-build
export XDG_CACHE_HOME="$T/cache-stop"
before=$(builds)
setsid "$RUN" "$FW/neokit" s > "$T/out" 2>"$T/err" &
pid=$!
i=0; while [ "$(builds)" = "$before" ] && [ $i -lt 1000 ]; do sleep 0.005; i=$((i+1)); done
sleep 0.2
[ "$(leftovers)" = 1 ] && ok "the build runs in a directory of its own" || bad "build directory" "$(leftovers)"
/bin/kill -TERM -- "-$pid"
wait "$pid"; st=$?
[ "$st" = 143 ] && [ "$(leftovers)" = 0 ] && [ "$(exes)" = 0 ] &&
  ok "SIGTERM during the build exits 143 and removes the build directory" || bad "SIGTERM" "status $st, $(leftovers) left, $(exes) built, $(cat "$T/err")"

# ---- symbolic links to the launcher
export XDG_CACHE_HOME="$T/cache"
ln -s "$FW/neokit" "$T/bin/neokit"
ln -s ../bin/neokit "$T/links/nk"
ln -s "$T/sdk" "$T/sdk-link"
out=$("$RUN" "$T/bin/neokit" l); [ "$out" = "$(expect l)" ] && ok "bin/neokit -> framework/neokit finds the framework" || bad "symlink" "$out"
out=$("$RUN" "$T/links/nk" l); [ "$out" = "$(expect l)" ] && ok "a relative chain of links too" || bad "link chain" "$out"
out=$("$RUN" "$T/sdk-link/framework/neokit" l); [ "$out" = "$(expect l)" ] && ok "a linked directory resolves to the physical root" || bad "linked directory" "$out"
out=$(cd "$T/bin" && PATH="$T/bin:$PATH" "$RUN" neokit l); [ "$out" = "$(expect l)" ] && ok "\$0 without a slash is looked up on PATH" || bad "PATH \$0" "$out"
out=$(PATH="$T/bin:$PATH" neokit l); [ "$out" = "$(expect l)" ] && ok "run through PATH by the kernel" || bad "PATH exec" "$out"
out=$(cd "$FW" && "$RUN" ./neokit l); [ "$out" = "$(expect l)" ] && ok "run as ./neokit" || bad "./neokit" "$out"

# ---- finding the toolchain
fresh() { export XDG_CACHE_HOME="$T/cache-$1"; }
unset NEO_HOME
fresh sdk; ln -s "$T/tcB" "$T/sdk/neo"
out=$(PATH=$BASE_PATH launch d); [ "$out" = "$(expect d)" ] && [ "$(calls)" = tcB ] && ok "without NEO_HOME, the SDK's ../neo" || bad "SDK neo" "$(calls) $out"
fresh env; out=$(env NEO_HOME="$T/tcA" PATH=$BASE_PATH "$RUN" "$FW/neokit" d); [ "$(calls)" = tcA ] && ok "NEO_HOME comes first" || bad "NEO_HOME priority" "$(calls)"
fresh relative; out=$(cd "$T" && env NEO_HOME=tcD PATH=$BASE_PATH "$RUN" "$FW/neokit" d); [ "$out" = "$(expect d)" ] && [ "$(calls)" = tcD ] && ok "a relative NEO_HOME is relative to the working directory" || bad "relative NEO_HOME" "$(calls) $out"
ln -s "$T/tcD/neo" "$T/pathbin/neo"
fresh both; out=$(PATH="$T/pathbin:$BASE_PATH" launch d); [ "$(calls)" = tcB ] && ok "the SDK's ../neo comes before PATH" || bad "SDK before PATH" "$(calls)"
rm "$T/sdk/neo"; mkdir "$T/sdk/neo"
fresh path; out=$(PATH="$T/pathbin:$BASE_PATH" launch d); [ "$out" = "$(expect d)" ] && [ "$(calls)" = tcD ] && ok "then neo on PATH, after its links" || bad "PATH neo" "$(calls) $out"
fresh tilde; ln -s "$T/tcA" "$T/home/neo-tc"
out=$(env HOME="$T/home" NEO_HOME='~/neo-tc' PATH=$BASE_PATH "$RUN" "$FW/neokit" d); [ "$(calls)" = tcA ] && ok "NEO_HOME=~/... is expanded" || bad "tilde" "$(calls)"
out=$(PATH=$BASE_PATH launch d 2>"$T/err"); st=$?
[ "$st" = 1 ] && [ "$(cat "$T/err")" = "neokit: Set NEO_HOME to an installed native Neo toolchain; checked $T/sdk/neo" ] &&
  ok "no toolchain: compiler_env's message and status 1" || bad "no toolchain" "$st $(cat "$T/err")"
out=$(env NEO_HOME="$T/nowhere" "$RUN" "$FW/neokit" d 2>"$T/err"); st=$?
[ "$st" = 1 ] && [ "$(cat "$T/err")" = "neokit: Set NEO_HOME to an installed native Neo toolchain; checked $T/nowhere" ] &&
  ok "a NEO_HOME that is not a toolchain: the same" || bad "bad NEO_HOME" "$st $(cat "$T/err")"
rmdir "$T/sdk/neo"

# ---- the SDK's multi-platform toolchain (Neo's release archive)
if [ -n "$ARCHIVE" ]; then
  mkdir -p "$T/multi" "$T/fake"
  tar xzf "$ARCHIVE" -C "$T/multi"
  ln -s "../multi/$(ls "$T/multi" | head -n 1)" "$T/sdk/neo"
  fresh multi
  out=$(launch m); st=$?
  [ "$st" = 0 ] && [ "$out" = "$(expect m)" ] && [ "$(exes)" = 1 ] && ok "the SDK's multi-platform ../neo builds" || bad "multi-platform" "$st $out"
  exe=$(ls -d "$XDG_CACHE_HOME"/neokit/cli/*/neokit)
  started=$(now); i=0; while [ $i -lt $n ]; do launch x >/dev/null; i=$((i+1)); done; launched=$(( ($(now)-started)/n/1000 ))
  started=$(now); i=0; while [ $i -lt $n ]; do NEOKIT_ROOT=$FW "$exe" x >/dev/null; i=$((i+1)); done; direct=$(( ($(now)-started)/n/1000 ))
  ok "its cache hit (with uname): ${launched} us per run, ${direct} us directly ($((launched-direct)) us launcher)"
  printf '#!/bin/sh\n[ "$*" = -sm ] && { echo Other machine; exit; }\nexec /usr/bin/uname "$@"\n' > "$T/fake/uname"; chmod 755 "$T/fake/uname"
  out=$(PATH="$T/fake:$PATH" launch m); [ "$out" = "$(expect m)" ] && [ "$(exes)" = 2 ] && ok "... and another machine gets its own executable" || bad "machine key" "$(exes) $out"
  rm "$T/sdk/neo"
else
  echo "skip the multi-platform checks (NEO_ARCHIVE unset)"
fi

# ---- the real command line
if [ -n "$FULL" ]; then
  export NEO_HOME="$TOOLCHAIN" XDG_CACHE_HOME="$T/cache-full"
  cp "$REPO/cli/main.neo" "$FW/cli/main.neo"
  started=$(now); out=$(launch -h 2>"$T/err"); st=$?; took=$(( ($(now)-started)/1000000 ))
  exe=$(ls -d "$XDG_CACHE_HOME"/neokit/cli/*/neokit 2>/dev/null)
  [ "$st" = 0 ] && [ "${out%%"$nl"*}" = "usage: neokit [-h]" ] && ok "cli/main.neo builds in ${took} ms ($(wc -c < "$exe") bytes) and prints its help" || bad "full build" "$st $out $(cat "$T/err")"
  started=$(now); i=0; while [ $i -lt $n ]; do launch -h >/dev/null; i=$((i+1)); done; launched=$(( ($(now)-started)/n/1000 ))
  started=$(now); i=0; while [ $i -lt $n ]; do NEOKIT_ROOT=$FW "$exe" -h >/dev/null; i=$((i+1)); done; direct=$(( ($(now)-started)/n/1000 ))
  ok "its cache hit: ${launched} us per run, ${direct} us directly ($((launched-direct)) us launcher)"
  out=$(env -u NEOKIT_ROOT "$exe" 2>&1); st=$?
  [ "$st" = 1 ] && [ "${out#neokit: NEOKIT_ROOT is not set}" != "$out" ] && ok "the executable without the launcher names NEOKIT_ROOT and exits 1" || bad "no NEOKIT_ROOT" "$st $out"
fi
exit $FAILED
