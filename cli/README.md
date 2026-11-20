# The native `neokit` CLI

`neokit` is one native executable built from `cli/*.neo` with Neo. It needs
Neo 0.2.11 or newer (`fs_mkdir_mode` in std:fs, SIGINT in `process_signal`),
Bun and Clang/LLVM. The code
uses only `std:` modules, never `unsafe` or neo-libs packages.

## Modules

Imports are lexical with transitive re-exports, so every public function
carries its module's prefix. `cli_` is taken by std:io's `cli_arg`, and
`rt_`, `tx_`, `pt_`, `pf_` and `bd_` belong to Neo, so they are never used.

| Module | Prefix | What it holds |
|---|---|---|
| `main.neo` | `cmd_` | the entry point and the commands: build, serve, dev, export, test, init, release, doctor, prepare, revalidate, password, migrate |
| `args.neo` | `cmd_` | argument parsing, help and usage errors, in argparse's format |
| `preview.neo` | `cmd_` | `neokit preview`, a static file server for exports |
| `build.neo` | `gen_` | discovery, code generation, asset bundling and native builds |
| `release.neo` | `rel_` | the relocatable source SDK: assemble, bundle and the Neo archive |
| `scaffold.neo` | `scf_` | `neokit init` |
| `host.neo` | `host_` | paths, files, directories, processes and the environment |
| `templates.neo` | `tpl_` | the typed `.view` template compiler |
| `routes.neo` | `route_` | app folders to URL patterns, precedence and matching |
| `actions.neo` | `act_` | typed action adapters for `[input]` schemas |
| `assets.neo` | `meta_` | metadata, generated public files, compression and image variants |
| `toml.neo` | `toml_` | typed lookups over std:toml |
| `pyjson.neo` | `pj_` | JSON text in the layout of `manifest.json` and the other JSON NeoKit writes |
| `text.neo` | `text_` | string operations on UTF-8 bytes, with the Unicode tables they need |

Each module's header comment describes its contract, and the differences
from the former Python CLI where there are any.

## The launcher

`./neokit` is a POSIX sh script, and `./web` is a byte-for-byte copy of it
(`neo pkg lock` refuses a package that holds symbolic links). It:

1. finds the framework root: its own directory, after symbolic links;
2. finds a Neo toolchain: `NEO_HOME`, else the SDK's `../neo`, else the
   directory of `neo` on PATH after its links;
3. computes a cache key, the SHA-256 (or `cksum`) of `LAUNCHER_KEY`, the
   names and contents of `cli/*.neo`, and the toolchain: its `neo` launcher
   and `MANIFEST.json`, or without a manifest the listing of its compilers
   and its VERSION, stdlib and runtime; plus `uname -sm` when the toolchain
   carries compilers for several platforms. Other files in `cli/` are not
   part of the key, and compilers are never read, so a cache hit costs a
   few milliseconds;
4. on a miss, builds `cli/main.neo` into a directory of its own under
   `${XDG_CACHE_HOME:-$HOME/.cache}/neokit/cli/KEY/` and renames the result
   to `neokit` there, so runs that start at once each end with a whole
   executable (a failed build exits 1; SIGINT and SIGTERM remove the build
   directory);
5. execs the executable with `NEOKIT_ROOT` set to the framework root.

Bump `LAUNCHER_KEY` when the way the CLI is built changes. Everything
`cli/main.neo` imports must stay in `cli/`. One SDK archive therefore serves
every platform Neo supports, without NeoKit binaries per platform. Run
without the launcher, the executable exits 1 asking for `NEOKIT_ROOT`.

## Behaviour

- Errors. A failure is `Err(message)`, propagated with `?`; `main` prints
  `neokit: MESSAGE` on stderr and exits 1. Failed system calls read
  `[Errno N] TEXT: 'PATH'` (host.neo), failed build tools name the command
  and its status. Usage errors print argparse's usage and
  `neokit: error: ...` with status 2; help exits 0.
- Toolchain. `gen_compiler_env` uses `NEO_HOME`, else `ROOT/../neo`, else
  the directory of `neo` on PATH, and fails with "Set NEO_HOME to an
  installed native Neo toolchain; checked PATH". Builds run `NEO_HOME/neo`
  with `NEO_HOME` and `NEO_ROOT` set and `NEOC` removed.
- Signals. std:signals' monitor blocks SIGINT and SIGTERM; children started
  while it is open start with both unblocked (Neo 0.2.8). `serve` runs the
  server as a child in neokit's process group, passes SIGINT or SIGTERM on as
  the same signal (unless the server ends within 250 ms) and exits with the
  server's status, 128+N after signal N. `dev` keeps the monitor open while
  it watches and rebuilds; a reload or a signal stops the server with
  SIGTERM, then SIGKILL after 2 s, and a signal then exits 0 (SIGINT) or 143
  (SIGTERM); a rebuild that a signal cut short is not reported. `test`, `revalidate`
  and `migrate` give their program 250 ms after SIGINT, then SIGKILL, and
  exit 0. Builds, `init`, `release`, `prepare` and `doctor` take a signal's
  default action. The header of `main.neo` has the details.
- `neokit test` runs, from the root with the compiler's environment,
  `neo test . --unit-only`, then `neo test` on every
  `tests/cli_golden/*/*.neo.toml`, then `bun test ./client`, the
  query-builder native test (`tests/query_builder.neo`) and
  `bun test ./tests/core`. `--extended` adds `tests/extended`; `--postgres`
  adds `tests/postgres` in a private cluster (`tests/tools/postgres.ts`). It
  stops at the first failing step.

## Tests

- Unit tests: `tests/cli_<module>_test.neo`, zero-argument boolean `test_*`
  functions, run by `neo test . --unit-only`.
- Golden tests: `tests/cli_golden/<module>/<module>.neo.toml` manifests run
  a driver (`driver.neo`, or `effects.neo` for host) on the cases beside it
  and compare its output with `golden/`. The expectations were recorded
  from the Python reference (last present at commit 4757083) and are now
  fixed; change one only with the behaviour it records, and say why.
  The release and scaffold drivers pass release.neo the Neo version their
  stand-in archives were made with (0.2.6), not `rel_neo_version()`, so a
  Neo upgrade changes only the `--neo-archive` help text in the args golden.
  `tests/cli_golden/build/driver.neo` runs build.neo's entry points
  directly for debugging; no manifest runs it.
- The launcher: `sh tests/tools/launcher_test.sh [SHELL [full]]` from the
  root, with `NEO_HOME` set, checks building, cache keys, failures,
  concurrent first runs, signals, symbolic links and toolchain lookup in a
  framework copy under `build/launcher-test` (see its header;
  `NEO_ARCHIVE` adds the multi-platform SDK checks). Remove that directory
  afterwards: it holds symbolic links, which `neo pkg lock` refuses.
- Everything else (`tests/core`, `tests/extended`, `tests/postgres`) drives
  the CLI through `./neokit`.

## Differences from the former Python CLI

Commands, options, defaults, output, files and exit statuses are the same,
except:

- Gzip variants come from std:deflate's `gzip_encode`: the same payload and
  headers, other compressed bytes. The SDK's tar is the same before
  compression.
- There is no Brotli encoder, so builds write no `.br` variants.
- Responsive images come from libvips (`vips` and `vipsheader` on PATH)
  instead of Pillow: the same sizes, formats, cache keys and URLs, other
  encoded bytes.
- Without `COLUMNS`, help and usage wrap at 80 columns; the former CLI read
  the terminal's width.
- `neokit password` reads the terminal through stdin and prompts on stderr,
  where getpass used `/dev/tty`; without a terminal it warns and reads a
  line of stdin; input that ends early is an error, not a traceback.
- `serve` runs the server as a child instead of exec'ing it, so a signal
  that kills the server ends neokit with 128+N. SIGTERM stops a dev server
  or test program before neokit exits 143.
- `dev` keeps watching after a failed system call during a rebuild, and
  gives `NEOKIT_DEV_STATUS` to the server only.
- `preview` serves only within its directory (no links outside it), with
  `neokit serve`'s content types, UTC log times and bounded connections.
- Directory modes follow the umask where the former CLI chmod-ed, and
  copied files keep no modification time.
- A few malformed configurations that crashed the former CLI with a
  traceback are reported as errors.
- With `[security] csp`, the generated server also puts the policy in the
  head of streamed responses (`neokit_stream_policy`), their 405 replies
  and the 503 sent when async capacity runs out, which the former CLI left
  with Neo's default. Without it, the generated code is the same.
- `doctor` prints no interpreter line.

The module headers (`host.neo`, `args.neo`, `assets.neo`, `preview.neo`,
`release.neo`, `main.neo`, `toml.neo`) list the rest.
