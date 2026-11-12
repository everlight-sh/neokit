#!/usr/bin/env bun
// CLI parity harness: compiler/cli.py (the reference) against the native CLI
// (./neokit, which runs cli/main.neo).
//
// Run on the Linux test box from the repository root, with NEO_HOME set:
//   bun tests/tools/cli_parity.ts [--only 'TEXT[|TEXT...]'] [--keep] [--show]
//
// Every case runs one invocation through both CLIs, Python first, with the
// same arguments, working directory, environment and output paths, and
// compares stdout, stderr and the exit status. Only timings are masked
// (parity.ts's steady()). Cases that write a tree (an export, an init, a
// migration's data directory, a release archive) also compare it: Python's
// tree is moved aside before the native run and the two are compared by
// path, kind, mode and content. Everything a case writes is removed
// afterwards (--keep leaves the trees of failing cases). --only runs the
// cases whose names contain one of the texts. --show prints the
// start of each native stdout and the end of its stderr.
//
// Python runs as `python3 -c "from compiler.cli import main; main()" ARGS`
// from the repository root: compiler/cli.py has no __main__ guard, so
// `python3 -m compiler.cli` imports it and exits 0 without doing anything.
// Brotli is hidden from Python as in parity.ts, as the native build has no
// Brotli encoder.
//
// Documented differences, applied only to the cases named:
// - release: the archive differs by design (the native SDK carries cli/
//   where release.py's carries compiler/, adds VERSION and SHA256SUMS, and
//   std:deflate's stream differs from zlib's), so the printed sha256 is
//   masked after checking it against each side's own archive, and the
//   archives are compared by their member lists with those differences
//   allowed.
// - doctor: the native CLI has no Python line; it is dropped from Python's
//   output before comparing.
// - password (run without a terminal): see passwordMask.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import { gunzipSync } from "node:zlib";

const ROOT = resolve(import.meta.dir, "../..");
const WORK = join(ROOT, "build/cli-parity");
const NOBROTLI = join(WORK, "nobrotli");
// init runs `neo pkg lock` against the framework, so its targets live outside
// the NeoKit package.
const OUTSIDE = mkdtempSync(join(tmpdir(), "neokit-cli-parity-"));
const NEO_ARCHIVE = process.env.NEO_ARCHIVE ?? "/root/neokit-audit/neo-0.2.6-dl/neo-0.2.6.tar.gz";
const NEO_SHA256 = process.env.NEO_SHA256 ?? "b360276965c3989dd29b583ed40d1395a1e52ce8d1c3e3c0a16882009c79e98f";

const argv = process.argv.slice(2);
const option = (name: string) => { const at = argv.indexOf(name); return at >= 0 ? argv[at + 1] : undefined; };
const flag = (name: string) => argv.includes(name);
const only = option("--only")?.split("|");

// ---------------------------------------------------------------- processes

type Ran = { status: number; stdout: string; stderr: string; ms: number };
type Env = Record<string, string | undefined>;
function run(command: string[], cwd: string, env: Env, input?: string): Ran {
  const merged: Record<string, string> = {};
  for (const [key, value] of Object.entries({ ...process.env, ...env })) if (value !== undefined) merged[key] = value;
  const start = performance.now();
  const result = spawnSync(command[0], command.slice(1), { cwd, env: merged, encoding: "utf8", maxBuffer: 256 << 20, stdio: [input === undefined ? "ignore" : "pipe", "pipe", "pipe"], input });
  if (result.error) throw result.error;
  return { status: result.status ?? -1, stdout: result.stdout, stderr: result.stderr, ms: Math.round(performance.now() - start) };
}
type Side = "Python" | "native";
const PY_MAIN = "from compiler.cli import main; main()";
function cli(side: Side, args: string[], cwd: string, env: Env, input?: string): Ran {
  return side === "Python"
    ? run(["python3", "-c", PY_MAIN, ...args], cwd, { PYTHONPATH: NOBROTLI + ":" + ROOT, PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1", ...env }, input)
    : run([join(ROOT, "neokit"), ...args], cwd, env, input);
}
// Timings and bun's bundle report vary from run to run.
const steady = (text: string) => text.replace(/\d+(\.\d+)?ms\b/g, "Nms").replace(/^Bundled \d+ modules in .*$/gm, "Bundled N modules");

// ---------------------------------------------------------------- trees

type Entry = { kind: "dir" | "file" | "link"; mode: number };
function walk(top: string): Map<string, Entry> {
  const found = new Map<string, Entry>();
  const visit = (folder: string) => {
    for (const name of readdirSync(folder).sort()) {
      const path = join(folder, name);
      const stat = lstatSync(path);
      const kind = stat.isSymbolicLink() ? "link" : stat.isDirectory() ? "dir" : "file";
      found.set(relative(top, path), { kind, mode: stat.mode & 0o7777 });
      if (kind === "dir") visit(path);
    }
  };
  visit(top);
  return found;
}
function sameTree(left: string, right: string, contents = true): string[] {
  if (!existsSync(left) && !existsSync(right)) return [];
  if (!existsSync(left) || !existsSync(right)) return [`${right}: ${existsSync(left) ? "only Python" : "only native"} wrote it`];
  const top = lstatSync(left), other = lstatSync(right);
  if (!top.isDirectory() || !other.isDirectory()) {
    const problems = (top.mode & 0o7777) !== (other.mode & 0o7777) ? [`${right}: mode ${(top.mode & 0o7777).toString(8)} against ${(other.mode & 0o7777).toString(8)}`] : [];
    if (contents && !readFileSync(left).equals(readFileSync(right))) problems.push(`${right} differs`);
    return problems;
  }
  const problems: string[] = [];
  if ((top.mode & 0o7777) !== (other.mode & 0o7777)) problems.push(`${right}: mode ${(top.mode & 0o7777).toString(8)} against ${(other.mode & 0o7777).toString(8)}`);
  const a = walk(left), b = walk(right);
  for (const path of a.keys()) if (!b.has(path)) problems.push(`only in Python: ${path}`);
  for (const path of b.keys()) if (!a.has(path)) problems.push(`only in native: ${path}`);
  for (const [path, x] of a) {
    const y = b.get(path);
    if (!y) continue;
    if (x.kind !== y.kind || x.mode !== y.mode) problems.push(`${path}: ${x.kind} ${x.mode.toString(8)} against ${y.kind} ${y.mode.toString(8)}`);
    else if (contents && x.kind === "file" && !readFileSync(join(left, path)).equals(readFileSync(join(right, path)))) problems.push(`${path} differs`);
  }
  return problems;
}
const sha256 = (path: string) => createHash("sha256").update(readFileSync(path)).digest("hex");
function firstDifference(a: string, b: string): string {
  let i = 0; while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return `offset ${i}: Python …${JSON.stringify(a.slice(Math.max(0, i - 60), i + 80))}… native …${JSON.stringify(b.slice(Math.max(0, i - 60), i + 80))}…`;
}
// The member names of a USTAR archive (gzip-compressed).
function tarMembers(path: string): string[] {
  const data = gunzipSync(readFileSync(path));
  const names: string[] = [];
  for (let at = 0; at + 512 <= data.length;) {
    const header = data.subarray(at, at + 512);
    if (header.every(byte => byte === 0)) break;
    const field = (start: number, size: number) => { const raw = header.subarray(start, start + size); const end = raw.indexOf(0); return raw.subarray(0, end < 0 ? size : end).toString(); };
    const prefix = field(345, 155), name = field(0, 100);
    names.push(prefix ? prefix + "/" + name : name);
    at += 512 + Math.ceil(parseInt(field(124, 12).trim() || "0", 8) / 512) * 512;
  }
  return names;
}

// ---------------------------------------------------------------- cases

type Case = {
  name: string;
  args: string[];
  cwd?: string;
  env?: Env;
  // Standard input (none: /dev/null).
  input?: string;
  // Made before each side runs (after the case's paths are removed).
  setup?: () => void;
  // Trees both sides write; compared after both ran.
  trees?: string[];
  // Trees compared by path, kind and mode only.
  shapes?: string[];
  // Also removed before each side and at the end.
  clean?: string[];
  // Output rewriting for a documented difference.
  mask?: (text: string, side: Side) => string;
  // Extra checks after both ran.
  check?: (py: Ran, neo: Ran) => { problems: string[]; notes: string[] };
};

const out = (name: string) => join(WORK, "out", name);
const blog = join(ROOT, "examples/blog");
const features = join(ROOT, "examples/features");
const dashboard = join(ROOT, "examples/dashboard");
const shared = out("blog-shared");
const noToken: Env = { NEOKIT_REVALIDATE_TOKEN: undefined };
const write = (path: string, text: string) => { mkdirSync(join(path, ".."), { recursive: true }); writeFileSync(path, text); };
// password: Python's getpass also warns through the warnings module (a
// GetPassWarning line naming its own source), and an input that ends early
// is an uncaught EOFError (a traceback) where the native CLI prints
// "neokit: EOF when reading a line"; both exit 1. The hash is salted, so
// only its shape is compared.
const passwordMask = (text: string, side: Side) =>
  (side === "Python" ? text.replace(/^.*GetPassWarning: .*\n(?:  .*\n)?/m, "").replace(/Traceback \(most recent call last\):\n[\s\S]*\nEOFError\n$/, "neokit: EOF when reading a line\n") : text)
    .replace(/[A-Za-z0-9+\/=_.$-]{24,}/g, "<hash>");

const cases: Case[] = [
  // Argument errors and help, before anything is built.
  { name: "no arguments", args: [] },
  { name: "-h", args: ["-h"] },
  { name: "-h, COLUMNS=60", args: ["-h"], env: { COLUMNS: "60" } },
  { name: "build -h", args: ["build", "-h"] },
  { name: "export -h", args: ["export", "-h"] },
  { name: "release -h", args: ["release", "-h"] },
  { name: "unknown command", args: ["deploy"] },
  { name: "doctor x", args: ["doctor", "x"] },
  { name: "test --bogus", args: ["test", "--bogus"] },
  { name: "test -h", args: ["test", "-h"] },
  { name: "password x", args: ["password", "x"] },
  { name: "build --profile x", args: ["build", "--profile", "x"] },
  { name: "serve --port abc", args: ["serve", "--port", "abc"] },
  { name: "init without a path", args: ["init"] },
  { name: "release without --out", args: ["release"] },
  { name: "test --postgres without initdb", args: ["test", "--postgres"], env: { NEOKIT_PG_BIN: "/nonexistent" } },
  { name: "serve --no-build, no server", args: ["serve", "--no-build", "--out", out("missing")] },
  { name: "preview, no export", args: ["preview", "--dir", out("missing")] },
  { name: "build, missing app", args: ["build", "--app", join(WORK, "no-such-app"), "--out", out("missing")], clean: [out("missing")] },
  {
    name: "doctor", args: ["doctor"],
    mask: (text, side) => side === "Python" ? text.replace(/^Python 3\.[^\n]*\n/m, "") : text,
  },

  // password without a terminal: getpass's fallback reads stdin.
  { name: "password, mismatch", args: ["password"], input: "first\nsecond\n", mask: passwordMask },
  { name: "password", args: ["password"], input: "correct horse\ncorrect horse\n", mask: passwordMask },
  { name: "password, no input", args: ["password"], input: "", mask: passwordMask },

  // init
  { name: "init", args: ["init", join(OUTSIDE, "app")], trees: [join(OUTSIDE, "app")] },
  { name: "init, non-empty directory", args: ["init", join(OUTSIDE, "full")], clean: [join(OUTSIDE, "full")], setup: () => write(join(OUTSIDE, "full/keep.txt"), "keep\n") },
  { name: "init, a file", args: ["init", join(OUTSIDE, "file")], clean: [join(OUTSIDE, "file")], setup: () => write(join(OUTSIDE, "file"), "file\n") },

  // build
  { name: "build blog", args: ["build", "--app", blog, "--out", out("blog")], clean: [out("blog")] },
  { name: "build features --profile dev", args: ["build", "--app", features, "--out", out("features"), "--profile", "dev"], clean: [out("features")] },

  // export
  {
    name: "export (defaults, from examples/blog)", args: ["export"], cwd: blog,
    trees: [join(ROOT, "examples/blog-export")], clean: [join(blog, ".neokit")],
  },
  { name: "export features", args: ["export", "--app", features, "--out", out("features"), "--dest", join(WORK, "dest/features")], trees: [join(WORK, "dest/features")], clean: [out("features")] },
  { name: "export --strict, omitted routes", args: ["export", "--app", features, "--out", out("features"), "--dest", join(WORK, "dest/strict"), "--strict"], trees: [join(WORK, "dest/strict")], clean: [out("features")] },
  { name: "export --strict, nothing omitted", args: ["export", "--app", blog, "--out", shared, "--dest", join(WORK, "dest/blog-strict"), "--strict"], trees: [join(WORK, "dest/blog-strict")] },
  {
    name: "export over a previous export", args: ["export", "--app", blog, "--out", shared, "--dest", join(WORK, "dest/again")], trees: [join(WORK, "dest/again")],
    setup: () => { write(join(WORK, "dest/again/.neokit-export"), "neokit v0.1\n"); write(join(WORK, "dest/again/stale.html"), "old\n"); },
  },
  { name: "export --dest inside --out", args: ["export", "--app", blog, "--out", shared, "--dest", join(shared, "export")] },
  { name: "export --dest is --out", args: ["export", "--app", blog, "--out", shared, "--dest", shared] },
  { name: "export --dest is the app", args: ["export", "--app", blog, "--out", shared, "--dest", blog] },
  { name: "export --dest is the framework", args: ["export", "--app", blog, "--out", shared, "--dest", ROOT] },
  {
    name: "export --dest non-empty", args: ["export", "--app", blog, "--out", shared, "--dest", join(WORK, "dest/full")], trees: [join(WORK, "dest/full")],
    setup: () => write(join(WORK, "dest/full/keep.txt"), "keep\n"),
  },
  {
    name: "export --dest a file", args: ["export", "--app", blog, "--out", shared, "--dest", join(WORK, "dest/file")], trees: [join(WORK, "dest/file")],
    setup: () => write(join(WORK, "dest/file"), "file\n"),
  },

  // serve and dev that stop before serving
  { name: "serve --port 0", args: ["serve", "--app", blog, "--out", out("serve"), "--port", "0"], clean: [out("serve")] },
  { name: "dev --port 70000", args: ["dev", "--app", blog, "--out", out("dev"), "--port", "70000"], clean: [out("dev")] },

  // migrate
  { name: "migrate --build-only", args: ["migrate", "--app", dashboard, "--out", out("migrations"), "--build-only"], clean: [out("migrations")] },
  { name: "migrate without NEOKIT_DATA_DIR (fails)", args: ["migrate", "--app", dashboard, "--out", out("migrations")], env: { NEOKIT_DATA_DIR: undefined }, clean: [out("migrations")] },
  {
    name: "migrate, NEOKIT_DATA_DIR a file (fails)", args: ["migrate", "--app", dashboard, "--out", out("migrations")], env: { NEOKIT_DATA_DIR: join(WORK, "data-file") },
    clean: [out("migrations"), join(WORK, "data-file")], setup: () => write(join(WORK, "data-file"), "file\n"),
  },
  {
    name: "migrate", args: ["migrate", "--app", dashboard, "--out", out("migrations")], env: { NEOKIT_DATA_DIR: join(WORK, "data/new") },
    shapes: [join(WORK, "data")], clean: [out("migrations")],
  },
  { name: "migrate, app without migrate.neo", args: ["migrate", "--app", blog, "--out", out("migrations")], clean: [out("migrations")] },

  // revalidate
  { name: "revalidate, no stale_ttl routes", args: ["revalidate", "--app", blog, "--out", out("revalidation")], env: noToken, clean: [out("revalidation")] },
  { name: "revalidate, no token", args: ["revalidate", "--app", features, "--out", out("revalidation")], env: noToken, clean: [out("revalidation")] },
  { name: "revalidate, short token", args: ["revalidate", "--app", features, "--out", out("revalidation")], env: { NEOKIT_REVALIDATE_TOKEN: "short" }, clean: [out("revalidation")] },

  // release
  {
    name: "release with the Neo 0.2.6 archive",
    args: ["release", "--out", join(WORK, "release/neokit.tar.gz"), "--neo-archive", NEO_ARCHIVE, "--neo-sha256", NEO_SHA256],
    env: { SOURCE_DATE_EPOCH: "1700000000" }, clean: [join(WORK, "release")],
    mask: (text, side) => {
      const archive = join(WORK, "release/neokit.tar.gz");
      const match = /"sha256": "([0-9a-f]{64})"/.exec(text);
      const own = side === "Python" ? archive + "-py" : archive;
      const verdict = match && existsSync(own) && sha256(own) === match[1] ? "matches its archive" : "DOES NOT MATCH ITS ARCHIVE";
      return text.replace(/"sha256": "[0-9a-f]{64}"/, `"sha256": "(${verdict})"`);
    },
    check: () => {
      const archive = join(WORK, "release/neokit.tar.gz");
      if (!existsSync(archive) || !existsSync(archive + "-py")) return { problems: ["an archive is missing"], notes: [] };
      const py = new Set(tarMembers(archive + "-py")), neo = new Set(tarMembers(archive));
      const onlyPy = [...py].filter(name => !neo.has(name)), onlyNeo = [...neo].filter(name => !py.has(name));
      const allowed = (name: string, side: Side) => {
        const path = name.replace(/^neokit-[^/]+\//, "");
        return side === "Python" ? path.startsWith("framework/compiler/")
          : path.startsWith("framework/cli/") || (path.startsWith("framework/tests/") && path.endsWith(".ts")) || path === "VERSION" || path === "SHA256SUMS";
      };
      const problems = [...onlyPy.filter(name => !allowed(name, "Python")).map(name => `only in Python's archive: ${name}`),
                        ...onlyNeo.filter(name => !allowed(name, "native")).map(name => `only in the native archive: ${name}`)];
      return { problems, notes: [`${py.size} Python members, ${neo.size} native; ${onlyPy.length} only in Python's (compiler/), ${onlyNeo.length} only in the native one (cli/, tests/*.ts, VERSION, SHA256SUMS)`] };
    },
  },
  {
    name: "release, wrong --neo-sha256",
    args: ["release", "--out", join(WORK, "release/bad.tar.gz"), "--neo-archive", NEO_ARCHIVE, "--neo-sha256", "0".repeat(64)],
    trees: [join(WORK, "release")],
  },
  { name: "release, missing archive", args: ["release", "--out", join(WORK, "release/missing.tar.gz"), "--neo-archive", join(WORK, "no-such.tar.gz")], trees: [join(WORK, "release")] },
];

// ---------------------------------------------------------------- main

type Row = { name: string; verdict: "same" | "same except documented differences" | "different"; status: string; ms: string; problems: string[]; notes: string[]; shown: string[] };
function runCase(c: Case): Row {
  const cwd = c.cwd ?? ROOT;
  const paths = [...(c.trees ?? []), ...(c.shapes ?? []), ...(c.clean ?? [])];
  const remove = () => { for (const path of paths) rmSync(path, { recursive: true, force: true }); };
  const ran: Ran[] = [];
  const release = c.name.startsWith("release with");
  for (const side of ["Python", "native"] as Side[]) {
    remove();
    c.setup?.();
    ran.push(cli(side, c.args, cwd, c.env ?? {}, c.input));
    if (side === "Python") {
      for (const path of [...(c.trees ?? []), ...(c.shapes ?? [])]) {
        rmSync(path + "-py", { recursive: true, force: true });
        if (existsSync(path)) renameSync(path, path + "-py");
      }
      if (release) {
        const archive = join(WORK, "release/neokit.tar.gz");
        if (existsSync(archive)) { mkdirSync(join(WORK, "release-py"), { recursive: true }); renameSync(archive, join(WORK, "release-py/neokit.tar.gz")); }
      }
    }
  }
  if (release && existsSync(join(WORK, "release-py/neokit.tar.gz"))) {
    mkdirSync(join(WORK, "release"), { recursive: true });
    renameSync(join(WORK, "release-py/neokit.tar.gz"), join(WORK, "release/neokit.tar.gz-py"));
  }
  const [py, neo] = ran;
  const shown = (r: Ran, side: Side) => { const text = (s: string) => steady(c.mask ? c.mask(s, side) : s); return { stdout: text(r.stdout), stderr: text(r.stderr) }; };
  const a = shown(py, "Python"), b = shown(neo, "native");
  const problems: string[] = [];
  if (py.status !== neo.status) problems.push(`exit status: Python ${py.status}, native ${neo.status}`);
  if (a.stdout !== b.stdout) problems.push(`stdout: ${firstDifference(a.stdout, b.stdout)}`);
  if (a.stderr !== b.stderr) problems.push(`stderr: ${firstDifference(a.stderr, b.stderr)}`);
  for (const path of c.trees ?? []) problems.push(...sameTree(path + "-py", path));
  for (const path of c.shapes ?? []) problems.push(...sameTree(path + "-py", path, false));
  const extra = c.check ? c.check(py, neo) : { problems: [], notes: [] };
  problems.push(...extra.problems);
  const documented = !!c.mask && (a.stdout !== steady(py.stdout) || a.stderr !== steady(py.stderr) || b.stdout !== steady(neo.stdout));
  if (!problems.length || !flag("--keep")) {
    remove();
    for (const path of [...(c.trees ?? []), ...(c.shapes ?? [])]) rmSync(path + "-py", { recursive: true, force: true });
    if (release) rmSync(join(WORK, "release-py"), { recursive: true, force: true });
  }
  return {
    name: c.name, verdict: problems.length ? "different" : documented ? "same except documented differences" : "same",
    status: `${py.status}/${neo.status}`, ms: `${py.ms}/${neo.ms}`, problems, notes: extra.notes,
    shown: flag("--show") ? [...b.stdout.split("\n").slice(0, 2), ...b.stderr.split("\n").slice(-3)].filter(Boolean).map(line => "| " + line.slice(0, 200)) : [],
  };
}

// The default export writes beside examples/blog; keep whatever was there.
const saved: string[] = [];
function setup() {
  mkdirSync(WORK, { recursive: true });
  mkdirSync(NOBROTLI, { recursive: true });
  writeFileSync(join(NOBROTLI, "brotli.py"), 'raise ImportError("brotli hidden for parity")\n');
  for (const path of [join(blog, ".neokit"), join(ROOT, "examples/blog-export")]) {
    if (!existsSync(path)) continue;
    const aside = join(WORK, "saved", relative(ROOT, path).replaceAll("/", "_"));
    rmSync(aside, { recursive: true, force: true });
    mkdirSync(join(WORK, "saved"), { recursive: true });
    renameSync(path, aside);
    saved.push(path);
  }
  console.log("Building the native CLI…");
  const built = run([join(ROOT, "neokit"), "-h"], ROOT, {});
  if (built.status !== 0) { console.error(built.stdout + built.stderr); process.exit(1); }
}
function restore() {
  for (const path of saved) {
    rmSync(path, { recursive: true, force: true });
    renameSync(join(WORK, "saved", relative(ROOT, path).replaceAll("/", "_")), path);
  }
}

setup();
const rows: Row[] = [];
try {
  for (const c of cases) {
    if (only && !only.some(part => c.name.includes(part))) continue;
    const row = runCase(c);
    rows.push(row);
    console.log(`${row.verdict.padEnd(34)} ${row.status.padEnd(7)} ${row.ms.padStart(13)} ms  ${row.name}`);
    for (const line of [...row.problems, ...row.notes, ...row.shown]) console.log("    " + line);
  }
} finally {
  restore();
  rmSync(OUTSIDE, { recursive: true, force: true });
  if (!flag("--keep")) rmSync(WORK, { recursive: true, force: true });
}
const different = rows.filter(row => row.verdict === "different");
console.log(`\n${rows.length} invocations: ${rows.filter(r => r.verdict === "same").length} the same, ` +
  `${rows.filter(r => r.verdict === "same except documented differences").length} the same except documented differences, ${different.length} different.`);
process.exit(different.length ? 1 : 0);
