# Native CLI port: working conventions

NeoKit's compiler and CLI (`compiler/*.py` and the Python `neokit` launcher)
are being replaced by one native `neokit` binary built from `cli/*.neo`. The
Python code stays the reference until parity is proven: for every app, the
native build must write byte-identical `generated/*.neo`, `manifest.json`,
`assets/` and `snapshots/`. Then the Python is deleted. This file is for the
people and agents doing the port; it goes away with the Python.

## Layout and names

- `cli/main.neo` is the entry point; each Python module becomes `cli/<name>.neo`.
- Imports are lexical with transitive re-exports, so every public function in
  a module carries that module's prefix:
  `text_` (text.neo), `toml_` (toml.neo), `pj_` (pyjson.neo),
  `tpl_` (templates.neo), `route_` (routes.neo), `act_` (actions.neo),
  `meta_` (metadata/assets), `gen_` (code generation), `cli_` is taken by
  std:io's `cli_arg`, so the command layer uses `cmd_`. Never use `rt_`
  (Neo runtime) or `tx_`/`pt_`/`pf_`/`bd_` (Neo compiler).
- Only `std:` modules. No `unsafe`, no neo-libs packages.

## Semantics

- A Python `BuildError(message)` becomes `Result<T,Arr u8 _>` with
  `Err(message)`, propagated with `let x=f()? in ...`. Keep the message text
  identical to `str(BuildError(...))`.
- TOML files are parsed into std:json `Json` values (`toml_parse`): tables are
  objects in source order, arrays of tables are arrays of objects, strings,
  integers and booleans map directly. NeoKit configs need nothing else; floats
  and dates are errors.
- Python dicts and lists that are written out (manifest.json, export
  manifests) are built as `Json` and printed with `pyjson.neo`, which matches
  `json.dumps(value, indent=2)` byte for byte (`ensure_ascii` escaping, `,`
  and `: ` separators, two-space indentation, `[]`/`{}` for empty
  containers), plus the trailing newline the Python adds. Where the Python
  calls `json.dumps` without `indent` (separators `, ` and `: `) or with
  other options, match that call.
- Ordering: Python `sorted()` on str is code-point order, which is UTF-8 byte
  order; dict iteration is insertion order.
- Strings are UTF-8 `Arr u8 _`. Python `str` methods that are Unicode-aware
  (`isidentifier`, `lower`, `strip`) must be matched exactly for the inputs
  NeoKit accepts; reject or document anything wider.

## Neo APIs for the CLI layer

The CLI needs Neo 0.2.6 (released) plus `fs_mkdir_mode`, which ships in
0.2.7: `fs_mkdir_mode(path, mode) -> IntResult` passes the mode to mkdir(2)
(less the umask; error 22 outside 0..0o7777). Until 0.2.7 is out, build and
test the CLI against the branch build on the test box,
`/root/neo-dev/neo-feat` (read-only: `NEO_HOME=/root/neo-dev/neo-feat`,
`$NEO_HOME/neo ...`, reports 0.2.7-dev). NeoKit's own suites run on 0.2.6
(`/root/neokit-audit/neo-0.2.6`). The 0.2.6 additions the CLI uses:

- std:io: `env_list() -> Arr Arr u8 _ _` ("NAME=value", environ order, can be
  passed straight to process_start), `cli_count() -> i64`,
  `cli_args() -> Arr Arr u8 _ _` (argv[0] first), plus `cli_arg`, `env_get`.
- std:fs: `fs_list(path) -> FsList {ok, entries:Arr FsEntry {name, kind}, error}`
  (no "." or "..", unsorted, kinds do not follow links; error.code is errno:
  2 missing, 20 not a directory, 13 permission);
  `fs_stat(path, follow:bool) -> FsStat {ok, kind, size, mode, mtime_ns, error}`
  (mode = permission bits, mtime in ns); kinds `fs_kind_directory()` (4),
  `fs_kind_file()` (8), `fs_kind_symlink()` (10), others for devices, pipes
  and sockets; `fs_realpath(path) -> BytesResult` (path must exist);
  `fs_symlink(target, path) -> IntResult`.
- std:process: `process_start_inherit(path, args, environment) -> Process`
  (args exclude argv[0]; the child shares stdin/stdout/stderr; bind with
  `using`, then process_status/process_wait; the try_read/try_write calls
  return error 9 on it); `process_executable_path() -> BytesResult`.
- std:http_async: `http_async_start_at`, `http_render_start_at` and
  `http_render_start_with_tail_at` take extra `headers` ("Name: value\r\n"
  lines, checked like web_with_header; a Cache-Control, CSP or Date line
  replaces the default) and a Unix-ms `stamp` for Date (-1 for none).
- std:toml: `toml_parse(text) -> JsonResult` (TOML 1.0; objects keep document
  order; errors carry the 1-based line in error.position). NeoKit uses it
  directly; cli/toml.neo only adds typed accessors and error wording, and
  must not define its own `toml_parse`.
- std:deflate: `gzip_encode(bytes, level)`, `zlib_encode`, `deflate_raw`
  (levels 0–9, deterministic: MTIME 0, no file name, OS 255). The output is
  valid gzip but not byte-identical to Python's zlib, and there is no Brotli
  encoder.

Parity therefore has two documented exceptions: precompressed assets are
compared after decompression (the same bytes, the same Content-Encoding and
headers), and the reference build runs without Python's optional `brotli`
module, because the native build will not produce `.br` variants. Everything
else, including every byte of generated code outside compressed payloads,
must match.

Only Linux x86-64 has run these so far; macOS and arm64 need Neo's CI.

## Build, command layer and distribution

The last Python modules are split between two ports that share this contract.

- `cli/host.neo` (`host_`): all file, path and process access (Python
  `pathlib`/`os`/`shutil`/`subprocess`/`tempfile` semantics, OSError wording).
  Other modules use it instead of their own copies.
- `cli/build.neo` (`gen_`) replaces `compiler/build.py`. The framework root
  (Python's `ROOT`) is an explicit `root` argument, never a global:
  - `gen_build(root, app, out, target, profile) -> Result<GenBuilt, Arr u8 _>`
    with `GenBuilt {out, manifest:Json}`; same files and prints as `build()`.
  - `gen_prepare(root)`, `gen_refresh_assets(root, app, out)`,
    `gen_build_migration(root, app, out) -> Result<path>`,
    `gen_native_build(root, source, target, profile, package_root)`,
    `gen_compiler_env(root) -> Result<env list>`, `gen_compiler_command(env)`.
  - `cache_identity` hashes the same bases as Python, so it matches while
    `compiler/` exists; after the Python is deleted, `cli/` takes its place.
- `cli/release.neo` (`rel_`) replaces `compiler/release.py`: `assemble`,
  `bundle` (USTAR tar byte-identical to Python's `tarfile` before compression,
  gzip via `gzip_encode`; `gzip_decode` reads the Neo archive). Installing no
  longer runs NeoKit code: `deploy/install.sh` is plain sh and checks the
  SDK's `SHA256SUMS` with `sha256sum -c` or `shasum -a 256 -c`.
- `cli/scaffold.neo` (`scf_`) replaces `compiler/scaffold.py`.
- `cli/main.neo` (`cmd_`) replaces `compiler/cli.py`: the same commands,
  options, defaults, output and exit codes. Errors print `neokit: MESSAGE` to
  stderr and exit 1; usage errors follow argparse's wording and exit 2.
- `neokit` is a POSIX sh launcher (no Python) and its single source (there
  is no cli/launcher.sh); `./web` is a byte-for-byte copy, as `neo pkg lock`
  refuses a package holding symbolic links. It finds the framework
  root (its own resolved directory) and a Neo toolchain (`NEO_HOME`, else the
  SDK's `../neo`, else `neo` on PATH), builds `cli/main.neo` on first use into
  `${XDG_CACHE_HOME:-$HOME/.cache}/neokit/cli/<key>/neokit` (key from the CLI
  sources and the toolchain; built beside, then renamed into place), and
  execs it with `NEOKIT_ROOT` set. A cache hit must cost only a few
  milliseconds. One SDK archive therefore serves every platform Neo supports,
  and no NeoKit binaries need building per platform.
  `tests/tools/launcher_test.sh` checks it (see its header).

Parity is checked by `tests/tools/parity.ts` on the box: every app with a
`web.toml` (examples/*, tests/*_fixture, tests/cli_golden/assets/apps/*) is
built by the Python reference and by the native build, both with
`NEO_HOME=/root/neo-dev/neo-feat`, and `generated/`, `manifest.json`,
`assets/` and `snapshots/` are compared. The Python reference runs with
`brotli` hidden (a `brotli.py` raising ImportError first on `PYTHONPATH`);
the box has python3-brotli installed. Gzip literals in generated code are
compared after decompression, image variants by format and dimensions, and
`server_bytes` only when neither applies.

`tests/tools/cli_parity.ts` compares the commands themselves: `compiler/cli.py`
(run as `python3 -c "from compiler.cli import main; main()"`, as the module
has no `__main__` guard) against `./neokit` over a table of invocations, on
stdout, stderr, exit status and the trees they write, with timings masked.

## Tests

- Unit tests: `tests/cli_<module>_test.neo`, zero-argument boolean
  `test_*` functions (Neo's default `tests/*_test.neo` glob).
- Golden tests compare against the Python reference. Generate goldens on the
  test box with a short Python script that calls the reference function on
  every input it sees in `examples/` and the test fixtures; keep the script
  and goldens under `tests/cli_golden/<module>/` so they can be regenerated,
  until the Python is deleted.
- Build and run everything on the test box, never on a laptop. See the brief
  you were given for the box directory and environment.
