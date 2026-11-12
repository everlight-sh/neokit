#!/usr/bin/env bun
// Build parity harness: compiler/build.py (the reference) against cli/build.neo.
//
// Run on the Linux test box from the repository root, with NEO_HOME set:
//   bun tests/tools/parity.ts [--only APP[,APP...]] [--targets server,static,all]
//                             [--dev-apps APP,...] [--no-matrix] [--no-errors] [--no-extras] [--keep]
//
// Every application with a web.toml (examples/*, tests/*_fixture,
// tests/cli_golden/assets/apps/*) plus generated ones (kitchen: the options no
// example uses; images: responsive images) is built by both sides into the
// same output path, one after the other, and the trees compared:
// generated/, manifest.json, assets/, snapshots/, .neokit-build and the mode
// of every file and directory. The documented exceptions are
// - gzip literals in generated code: compared after decompression;
// - responsive image variants (Pillow against libvips): compared by format
//   and dimensions (vipsheader), with their content-hashed names, URLs and
//   ETags mapped from one side to the other;
// - manifest server_bytes: compared only when neither exception applied;
// - manifest app.
// Broken applications (generated under build/parity/apps) check that both
// sides fail with the same exit status and stderr. prepare() and
// refresh_assets() are compared by their output, files and modes. Python runs
// with Brotli hidden, as the native build has no Brotli encoder; both sides
// use this process's NEO_HOME, NEOKIT_LIBRARY_CACHE and umask. The native side
// is tests/cli_golden/build/driver.neo, built once per run into
// build/parity/driver.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { gunzipSync } from "node:zlib";

const ROOT = resolve(import.meta.dir, "../..");
const WORK = join(ROOT, "build/parity");
const APPS = join(WORK, "apps");
const OUT = join(WORK, "out");
const DRIVER = join(WORK, "driver");
const NOBROTLI = join(WORK, "nobrotli");
const NEO_HOME = process.env.NEO_HOME ?? "";

const PY_DRIVER = `
import subprocess, sys
from compiler.build import BuildError, build, build_migration, library_cache, prepare, refresh_assets
try:
    mode = sys.argv[1]
    if mode == '--migrate':
        print(build_migration(sys.argv[2], sys.argv[3]))
    elif mode == '--prepare':
        prepare(); print(library_cache())
    elif mode == '--refresh':
        refresh_assets(sys.argv[2], sys.argv[3]); print(sys.argv[3]+'/assets')
    else:
        out, _ = build(sys.argv[1], sys.argv[2], target=sys.argv[3], profile=sys.argv[4]); print(out)
except (BuildError, subprocess.CalledProcessError, OSError) as error:
    print('neokit: '+str(error), file=sys.stderr); sys.exit(1)
`;

// ---------------------------------------------------------------- options

const argv = process.argv.slice(2);
const option = (name: string) => { const at = argv.indexOf(name); return at >= 0 ? argv[at + 1] : undefined; };
const flag = (name: string) => argv.includes(name);
const only = option("--only")?.split(",");
const targets = option("--targets")?.split(",") ?? ["server", "static", "all"];
const devApps = option("--dev-apps")?.split(",") ?? ["studio", "features", "kitchen"];

// ---------------------------------------------------------------- processes

type Ran = { status: number; stdout: string; stderr: string; ms: number };
function run(command: string[], options: { cwd?: string; env?: Record<string, string> } = {}): Ran {
  const start = performance.now();
  const result = spawnSync(command[0], command.slice(1), { cwd: options.cwd ?? ROOT, env: { ...process.env, ...options.env }, encoding: "utf8", maxBuffer: 256 << 20 });
  if (result.error) throw result.error;
  return { status: result.status ?? -1, stdout: result.stdout, stderr: result.stderr, ms: Math.round(performance.now() - start) };
}
const python = (args: string[], env: Record<string, string> = {}) => run(["python3", "-c", PY_DRIVER, ...args], { env: { PYTHONPATH: NOBROTLI + ":" + ROOT, PYTHONDONTWRITEBYTECODE: "1", ...env } });
const native = (args: string[], env: Record<string, string> = {}) => run([DRIVER, ...(args[0].startsWith("--") ? [args[0], ROOT, ...args.slice(1)] : [ROOT, ...args])], { env });
// Timings and bun's bundle report vary from run to run.
const steady = (text: string) => text.replace(/\d+(\.\d+)?ms\b/g, "Nms").replace(/^Bundled \d+ modules in .*$/gm, "Bundled N modules");

// ---------------------------------------------------------------- files

function write(path: string, text: string | Uint8Array) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, text); }
type Entry = { kind: "dir" | "file" | "link"; mode: number };
function walk(top: string, skip: Set<string>): Map<string, Entry> {
  const found = new Map<string, Entry>();
  const visit = (folder: string) => {
    for (const name of readdirSync(folder).sort()) {
      const path = join(folder, name);
      const rel = relative(top, path);
      if (skip.has(rel)) continue;
      const stat = lstatSync(path);
      const kind = stat.isSymbolicLink() ? "link" : stat.isDirectory() ? "dir" : "file";
      found.set(rel, { kind, mode: stat.mode & 0o7777 });
      if (kind === "dir") visit(path);
    }
  };
  visit(top);
  return found;
}
const sha256 = (data: Uint8Array) => createHash("sha256").update(data).digest("hex");

// ---------------------------------------------------------------- images

const IMAGE_URL = /^\/_web\/images\/(.+)-(\d+)-([0-9a-f]{12})\.(webp|jpeg|png|avif)$/;
type Images = { urls: Map<string, string>; hashes: Map<string, string>; variants: Set<string>; problems: string[] };
// Pairs the image variants of the two manifests by (stem, width, format).
function pairImages(py: any, neo: any, pyOut: string, neoOut: string): Images {
  const keyed = (files: Record<string, unknown>) => {
    const map = new Map<string, string>();
    for (const url of Object.keys(files)) { const m = IMAGE_URL.exec(url); if (m) map.set(`${m[1]} ${m[2]} ${m[4]}`, url); }
    return map;
  };
  const left = keyed(py?.files ?? {}), right = keyed(neo?.files ?? {});
  const images: Images = { urls: new Map(), hashes: new Map(), variants: new Set(), problems: [] };
  for (const [key, url] of right) {
    const other = left.get(key);
    if (!other) { images.problems.push(`image variant ${key} only in the native build`); continue; }
    images.urls.set(url, other);
    const a = readFileSync(join(pyOut, "snapshots", other)), b = readFileSync(join(neoOut, "snapshots", url));
    images.hashes.set(sha256(b), sha256(a));
    images.variants.add(sha256(a)); images.variants.add(sha256(b));
    const shapeA = imageShape(join(pyOut, "snapshots", other)), shapeB = imageShape(join(neoOut, "snapshots", url));
    if (shapeA !== shapeB) images.problems.push(`image ${other}: Python ${shapeA}, native ${shapeB}`);
  }
  for (const [key] of left) if (!right.has(key)) images.problems.push(`image variant ${key} only in the Python build`);
  return images;
}
function imageShape(path: string): string {
  const header = run(["vipsheader", path]);
  if (header.status !== 0) return "unreadable: " + header.stderr.trim();
  const m = /: (\d+)x(\d+) .*?, (\w+)$/.exec(header.stdout.trim());
  return m ? `${m[1]}x${m[2]} ${m[3]}` : header.stdout.trim();
}
function mapImageText(text: string, images: Images): string {
  for (const [from, to] of images.urls) text = text.split(from).join(to);
  for (const [from, to] of images.hashes) text = text.split(from.slice(0, 24)).join(to.slice(0, 24));
  return text;
}
const mapImagePath = (path: string, images: Images) => {
  for (const [from, to] of images.urls) if (path.endsWith(from.slice(1))) return path.slice(0, path.length - from.length + 1) + to.slice(1);
  return path;
};

// ---------------------------------------------------------------- generated code

// Neo string literals as cli/templates.neo writes them (\n \r \t \" \\ \xHH).
function decodeLiteral(body: string): Uint8Array {
  const out: number[] = [];
  for (let i = 0; i < body.length; i++) {
    const c = body.charCodeAt(i);
    if (c !== 92) { const bytes = Buffer.from(body[i], "utf8"); out.push(...bytes); continue; }
    const e = body[++i];
    if (e === "n") out.push(10); else if (e === "r") out.push(13); else if (e === "t") out.push(9);
    else if (e === "x") { out.push(parseInt(body.slice(i + 1, i + 3), 16)); i += 2; }
    else out.push(e.charCodeAt(0));
  }
  return Uint8Array.from(out);
}
type Normalized = { text: string; gzip: number };
// Replaces each gzip literal by its payload's hash and each image variant
// literal by a placeholder, after mapping image names and ETags.
function normalizeCode(source: string, images: Images): Normalized {
  let gzip = 0;
  const text = mapImageText(source, images).replace(/"((?:[^"\\]|\\.)*)"/g, (whole, body: string) => {
    if (!body.startsWith("\\x1f\\x8b")) {
      if (images.variants.size === 0 || body.length < 8) return whole;
      const data = decodeLiteral(body);
      return images.variants.has(sha256(data)) ? '"<image variant>"' : whole;
    }
    gzip++;
    try { return `"<gzip ${sha256(gunzipSync(decodeLiteral(body)))}>"`; } catch (error) { return `"<bad gzip ${error}>"`; }
  });
  return { text, gzip };
}
function firstDifference(a: string, b: string): string {
  let i = 0; while (i < a.length && i < b.length && a[i] === b[i]) i++;
  const line = a.slice(0, i).split("\n").length;
  return `line ${line}, offset ${i}: Python …${JSON.stringify(a.slice(Math.max(0, i - 60), i + 80))}… native …${JSON.stringify(b.slice(Math.max(0, i - 60), i + 80))}…`;
}

// ---------------------------------------------------------------- comparison

type Verdict = { result: "identical" | "identical except documented exceptions" | "different"; notes: string[]; problems: string[] };
const BINARIES = new Set(["server", "render", "paths", "revalidate", "migrate"]);
function compareTrees(pyOut: string, neoOut: string): Verdict {
  const notes: string[] = [], problems: string[] = [];
  const pyManifest = JSON.parse(readFileSync(join(pyOut, "manifest.json"), "utf8"));
  const neoManifest = JSON.parse(readFileSync(join(neoOut, "manifest.json"), "utf8"));
  const images = pairImages(pyManifest, neoManifest, pyOut, neoOut);
  problems.push(...images.problems);
  if (images.urls.size) notes.push(`${images.urls.size} image variants compared by format and size`);
  const skip = new Set([".image-cache"]);
  const left = walk(pyOut, skip);
  const right = new Map([...walk(neoOut, skip)].map(([path, entry]) => [mapImagePath(path, images), entry]));
  const rightRaw = new Map([...walk(neoOut, skip)].map(([path]) => [mapImagePath(path, images), path]));
  let gzipLiterals = 0, exceptions = images.urls.size > 0;
  for (const path of left.keys()) if (!right.has(path)) problems.push(`only in Python: ${path}`);
  for (const path of right.keys()) if (!left.has(path)) problems.push(`only in native: ${rightRaw.get(path)}`);
  for (const [path, a] of left) {
    const b = right.get(path);
    if (!b) continue;
    if (a.kind !== b.kind) { problems.push(`${path}: ${a.kind} against ${b.kind}`); continue; }
    if (a.mode !== b.mode) problems.push(`${path}: mode ${a.mode.toString(8)} against ${b.mode.toString(8)}`);
    if (a.kind !== "file" || BINARIES.has(path) || path === "manifest.json") continue;
    const dataA = readFileSync(join(pyOut, path)), dataB = readFileSync(join(neoOut, rightRaw.get(path)!));
    if (dataA.equals(dataB)) continue;
    if (images.variants.has(sha256(dataA)) && images.variants.has(sha256(dataB))) continue;
    if (path.endsWith(".neo")) {
      const na = normalizeCode(dataA.toString("utf8"), images), nb = normalizeCode(dataB.toString("utf8"), images);
      if (na.text === nb.text) { gzipLiterals += na.gzip; exceptions = true; continue; }
      problems.push(`${path} differs: ${firstDifference(na.text, nb.text)}`);
      continue;
    }
    problems.push(`${path} differs (${dataA.length} against ${dataB.length} bytes)`);
  }
  if (gzipLiterals) notes.push(`${gzipLiterals} gzip literals equal after decompression`);
  // manifest.json: byte for byte, or with the exceptions mapped.
  const rawA = readFileSync(join(pyOut, "manifest.json"), "utf8"), rawB = readFileSync(join(neoOut, "manifest.json"), "utf8");
  if (rawA !== rawB) {
    const shape = (manifest: any, mapped: boolean) => {
      const copy = JSON.parse(mapped ? mapImageText(JSON.stringify(manifest), images) : JSON.stringify(manifest));
      delete copy.app;
      if (exceptions) delete copy.server_bytes;
      for (const url of Object.keys(copy.files ?? {})) if (IMAGE_URL.test(url)) copy.files[url] = "<image variant>";
      return JSON.stringify(copy, null, 2);
    };
    const a = shape(pyManifest, false), b = shape(neoManifest, true);
    if (a !== b) problems.push(`manifest.json differs: ${firstDifference(a, b)}`);
    else {
      exceptions = true;
      if (pyManifest.server_bytes !== neoManifest.server_bytes) notes.push(`server_bytes ${pyManifest.server_bytes} against ${neoManifest.server_bytes}`);
    }
  }
  return { result: problems.length ? "different" : exceptions ? "identical except documented exceptions" : "identical", notes, problems };
}

// ---------------------------------------------------------------- generated applications

const LIB = JSON.stringify(join(ROOT, "src/lib.neo"));
function page(folder: string, toml: string, view: string, neo: string) {
  write(join(folder, "page.toml"), toml);
  write(join(folder, "page.view"), view);
  write(join(folder, "page.neo"), `import ${LIB}\n${neo}`);
}
function basicApp(app: string, web = "") {
  rmSync(app, { recursive: true, force: true });
  write(join(app, "web.toml"), web);
  write(join(app, "app/layout.view"), "@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html><head><title>{{ title }}</title></head><body>{{ content }}</body></html>\n");
  page(join(app, "app"), 'loader="basic_home"\n', "@props(title:text)\n<h1>{{ title }}</h1>\n",
    "type BasicProps = { title:Arr u8 _ }\nfn basic_home(request:WebRequest) -> BasicProps = BasicProps { title=\"Home\" }\n");
}
function textProps(name: string, loader: string, title: string) {
  return `type ${name} = { title:Arr u8 _ }\nfn ${loader}(request:WebRequest) -> ${name} = ${name} { title=${JSON.stringify(title)} }\n`;
}
// The options no example uses: redirects, CSP, every server hook and
// observability flag, CORS, paths_fn, query-keyed stale caching,
// cache_control, guards, static pages, error.view, head:html metadata,
// nested layouts and public files of many types.
function kitchenApp(app: string) {
  rmSync(app, { recursive: true, force: true });
  write(join(app, "web.toml"), `[site]
name="Kitchen"
url="https://kitchen.test/"
description="Every build option, \\"quoted\\" & <escaped>"
og_title="Kitchen sink"

[server]
access_log=true
timing=true
before="kitchen_before"
after="kitchen_after"
startup="kitchen_start"
shutdown_grace_ms=2500

[security]
csp="default-src 'self'; img-src 'self' data:"

[[redirects]]
from="/old"
to="/new?x=1"

[[redirects]]
from="/gone"
to="https://example.test:8443/landing"
status=301
query=false

[[redirects]]
from="/temp"
to="/"
status=307
`);
  write(join(app, "app/hooks.neo"), `import ${LIB}
fn kitchen_before(request:WebRequest) -> WebResponse = web_continue()
fn kitchen_after(request:WebRequest,response:WebResponse) -> WebResponse = web_with_header(response,"X-Kitchen","1")
effect fn kitchen_start() -> IntResult = int_ok(0)
fn kitchen_guard(request:WebRequest) -> WebResponse = if str_equal(web_request_header(request,"x-allow"),"1") then web_continue() else web_reply(404,"","text/plain")
`);
  const head = "@props(title:text,content:html,client:bool,mode:text,head:html)\n";
  write(join(app, "app/layout.view"), head + "<!doctype html><html><head><title>{{ title }}</title>{{ head }}</head><body>{{ content }}</body></html>\n");
  write(join(app, "app/docs/layout.view"), head + "<section class=\"docs\">{{ content }}</section>\n");
  write(join(app, "app/error.view"), "@props(status:int,message:text)\n<h1>{{ status }}</h1><p>{{ message }}</p>\n");
  write(join(app, "app/not-found.view"), "@props()\n<!doctype html><html><body><h1>Missing</h1></body></html>\n");
  write(join(app, "components/Badge.view"), "@props(label:text,content:html)\n<span class=\"badge\">{{ label }}{{ content }}</span>\n");
  page(join(app, "app"), `loader="kitchen_home"
client=true
[metadata]
robots="index, follow"
canonical="https://kitchen.test/"
og_image="https://kitchen.test/og.png"
[metadata.props]
description="summary"
jsonld="data"
`, "@props(title:text,summary:text,data:text)\n<h1>{{ title }}</h1>{% component Badge label=summary %}<b>!</b>{% endcomponent %}\n",
    "type HomeProps = { title:Arr u8 _, summary:Arr u8 _, data:Arr u8 _ }\nfn kitchen_home(request:WebRequest) -> HomeProps = HomeProps { title=\"Kitchen\", summary=\"All options\", data=\"{\\\"a\\\":1}\" }\n");
  page(join(app, "app/cached"), 'loader="kitchen_cached"\ncache_ttl=30\nstale_ttl=60\ncache_query=["q","page"]\ncache_tag="kitchen"\n', "@props(title:text)\n<h1>{{ title }}</h1>\n", textProps("CachedProps", "kitchen_cached", "Cached"));
  page(join(app, "app/plain-cache"), 'loader="kitchen_plain"\ncache_ttl=5\n', "@props(title:text)\n<h1>{{ title }}</h1>\n", textProps("PlainProps", "kitchen_plain", "Plain"));
  page(join(app, "app/fresh"), 'loader="kitchen_fresh"\ncache_control="private, max-age=5"\n', "@props(title:text)\n<h1>{{ title }}</h1>\n", textProps("FreshProps", "kitchen_fresh", "Fresh"));
  page(join(app, "app/guarded"), 'loader="kitchen_guarded"\nguard="kitchen_guard"\n', "@props(title:text)\n<h1>{{ title }}</h1>\n", textProps("GuardedProps", "kitchen_guarded", "Guarded"));
  page(join(app, "app/about"), 'loader="kitchen_about"\nmode="static"\n[metadata]\ndescription="About the kitchen"\n', "@props(title:text)\n<h1>{{ title }}</h1>\n", textProps("AboutProps", "kitchen_about", "About"));
  page(join(app, "app/items/[id]"), 'loader="kitchen_item"\nmode="static"\npaths=["/items/1"]\npaths_fn="kitchen_item_paths"\n', "@props(title:text,id:text)\n<h1>{{ title }} {{ id }}</h1>\n",
    `type ItemProps = { title:Arr u8 _, id:Arr u8 _ }\nfn kitchen_item(request:WebRequest) -> ItemProps = ItemProps { title="Item", id=web_param(request,"id").value }\nfn kitchen_item_paths() -> Arr Arr u8 _ _ = arr(i in 0..4) if i==0 || i==3 then "/items/2" else if i==1 then "/items/1" else "/items/3"\n`);
  page(join(app, "app/docs"), 'loader="kitchen_docs"\nasync=true\nasync_timeout_ms=5000\nexport=false\n', "@props(title:text)\n<h1>{{ title }}</h1>\n", textProps("DocsProps", "kitchen_docs", "Docs"));
  page(join(app, "app/docs/[slug]"), 'loader="kitchen_doc"\npaths=["/docs/intro","/docs/guide"]\n', "@props(title:text)\n<h1>{{ title }}</h1>\n", textProps("DocProps", "kitchen_doc", "Doc"));
  const api = (folder: string, toml: string) => {
    const handler = /handler="(\w+)"/.exec(toml)![1];
    write(join(app, folder, "route.toml"), toml);
    write(join(app, folder, "route.neo"), `import ${LIB}\nfn ${handler}(request:WebRequest) -> WebResponse = web_json("{\\"ok\\":true}")\n`);
  };
  api("app/api/open", 'handler="kitchen_open"\ncors="*"\nmethods=["GET","POST"]\n');
  api("app/api/partner", 'handler="kitchen_partner"\ncors=["https://a.test","http://b.test:8080"]\ncors_headers=["content-type","x-token"]\ncors_credentials=true\n');
  api("app/api/[name]", 'handler="kitchen_named"\nmethods=["GET","DELETE"]\n');
  write(join(app, "app/api/act/route.toml"), 'handler="kitchen_act"\ninput_type="ActInput"\ncsrf=false\ncors=["https://a.test"]\n[input]\nname={type="text",min=1,max=20}\n');
  write(join(app, "app/api/act/route.neo"), `import ${LIB}\ntype ActInput = { name:Arr u8 _ }\nfn kitchen_act(request:WebRequest,input:ActInput) -> WebResponse = web_json("{}")\n`);
  write(join(app, "styles/site.css"), "body{margin:0;font-family:system-ui}\n");
  write(join(app, "client/index.ts"), "const greeting: string = \"kitchen\";\nconsole.log(greeting);\n");
  const big = "NeoKit parity ".repeat(80) + "\n";
  const files: Record<string, string> = {
    "robots.txt": "User-agent: *\nDisallow: /private\n", "data.csv": "a,b\n1,2\n", "notes.md": "# Notes\n", "big.txt": big, "data.json": JSON.stringify({ text: big }),
    "feed.xml": "<?xml version=\"1.0\"?><feed/>", "app.wasm": "\0asm\x01\0\0\0", "doc.pdf": "%PDF-1.4\n", "font.woff2": "wOF2", "archive.tar.gz": "gz", "pic.svgz": "svgz",
    "unknown.qqq": "?", "plain": "no suffix", "movie.mp4": "mp4", "song.mp3": "mp3", "style.CSS": "b{}", "page.htm": "<p>", "script.cjs": "1", "icon.bmp": "BM",
    "font.ttf": "ttf", "font.otf": "otf", "data.yaml": "a: 1", "bundle.zip": "PK", "doc.rtf": "{\\rtf}", ".well-known/security.txt": "Contact: mailto:security@kitchen.test\n",
    "nested/deep/file.js": "export default 1;\n",
  };
  for (const [name, text] of Object.entries(files)) write(join(app, "public", name), text);
}
function imagesApp(app: string) {
  const source = join(ROOT, "tests/cli_golden/assets/apps/images");
  basicApp(app, readFileSync(join(source, "web.toml"), "utf8"));
  cpSync(join(source, "public"), join(app, "public"), { recursive: true });
}

type Broken = { name: string; make: (app: string) => void; target?: string; profile?: string; args?: (app: string, out: string) => string[]; env?: Record<string, string> };
const replace = (path: string, from: string, to: string) => writeFileSync(path, readFileSync(path, "utf8").replace(from, to));
const BROKEN: Broken[] = [
  { name: "missing-loader", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'mode="ssr"\n'); } },
  { name: "bad-cache-ttl", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'loader="basic_home"\ncache_ttl=-1\n'); } },
  { name: "text-cache-ttl", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'loader="basic_home"\ncache_ttl="10"\n'); } },
  { name: "stale-without-ttl", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'loader="basic_home"\nstale_ttl=5\n'); } },
  { name: "bad-cache-query", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'loader="basic_home"\ncache_ttl=5\ncache_query=["a","a"]\n'); } },
  { name: "bad-cache-control", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'loader="basic_home"\ncache_control="a\\nb"\n'); } },
  { name: "duplicate-route", make: app => { basicApp(app); page(join(app, "app/(a)/x"), 'loader="basic_home"\n', "@props(title:text)\n<p></p>\n", ""); page(join(app, "app/(b)/x"), 'loader="basic_home"\n', "@props(title:text)\n<p></p>\n", ""); } },
  { name: "page-and-api", make: app => { basicApp(app); page(join(app, "app/x"), 'loader="basic_home"\n', "@props(title:text)\n<p></p>\n", ""); write(join(app, "app/x/(api)/route.toml"), 'handler="h"\n'); } },
  { name: "param-shapes", make: app => { basicApp(app); write(join(app, "app/x/[a]/route.toml"), 'handler="h"\n'); write(join(app, "app/x/[b]/q/route.toml"), 'handler="h"\n'); page(join(app, "app/x/[c]"), 'loader="basic_home"\n', "@props(title:text)\n<p></p>\n", ""); } },
  { name: "bad-redirect-target", make: app => basicApp(app, '[[redirects]]\nfrom="/old"\nto="//evil.test"\n') },
  { name: "bad-redirect-source", make: app => basicApp(app, '[[redirects]]\nfrom="/caf\u00e9/"\nto="/"\n') },
  { name: "bad-redirect-status", make: app => basicApp(app, '[[redirects]]\nfrom="/old"\nto="/"\nstatus=200\n') },
  { name: "redirect-extra-key", make: app => basicApp(app, '[[redirects]]\nfrom="/old"\nto="/"\ncode=301\n') },
  { name: "duplicate-redirect", make: app => basicApp(app, '[[redirects]]\nfrom="/old"\nto="/"\n[[redirects]]\nfrom="/old"\nto="/a"\n') },
  { name: "redirect-onto-page", make: app => basicApp(app, '[[redirects]]\nfrom="/"\nto="/a"\n') },
  { name: "bad-csp", make: app => basicApp(app, '[security]\ncsp="default-src \'self\'\\n"\n') },
  { name: "empty-csp", make: app => basicApp(app, '[security]\ncsp=""\n') },
  { name: "security-extra", make: app => basicApp(app, '[security]\nhsts=true\n') },
  { name: "bad-hook", make: app => basicApp(app, '[server]\nafter="not a name"\n') },
  { name: "bad-grace", make: app => basicApp(app, '[server]\nshutdown_grace_ms=0\n') },
  { name: "bad-realtime", make: app => basicApp(app, '[realtime]\nhistory=2000\n') },
  { name: "big-realtime", make: app => basicApp(app, '[realtime]\nchannels=64\nhistory=1024\nmax_event_bytes=8192\n') },
  { name: "bad-css", make: app => basicApp(app, '[css]\ntailwind=["../x.css"]\n') },
  { name: "missing-tailwind-input", make: app => basicApp(app, '[css]\ntailwind=["site.css"]\n') },
  { name: "bad-cors", make: app => { basicApp(app); write(join(app, "app/api/route.toml"), 'handler="h"\ncors=["https://a.test/"]\n'); } },
  { name: "cors-options", make: app => { basicApp(app); write(join(app, "app/api/route.toml"), 'handler="h"\ncors="*"\nmethods=["GET","OPTIONS"]\n'); } },
  { name: "cors-credentials-star", make: app => { basicApp(app); write(join(app, "app/api/route.toml"), 'handler="h"\ncors="*"\ncors_credentials=true\n'); } },
  { name: "cors-typed-csrf", make: app => { basicApp(app); write(join(app, "app/api/route.toml"), 'handler="h"\ninput_type="I"\ncors="*"\n[input]\na="text"\n'); } },
  { name: "realtime-post", make: app => { basicApp(app); write(join(app, "app/api/route.toml"), 'handler="h"\nrealtime=true\nmethods=["POST"]\n'); } },
  { name: "reserved-route", make: app => { basicApp(app); write(join(app, "app/_web/x/route.toml"), 'handler="h"\n'); } },
  { name: "paths-mismatch", make: app => { basicApp(app); page(join(app, "app/p/[id]"), 'loader="basic_home"\npaths=["/q/caf\u00e9"]\n', "@props(title:text)\n<p></p>\n", ""); } },
  { name: "no-title-prop", make: app => { basicApp(app); writeFileSync(join(app, "app/page.view"), "@props(name:text)\n<p></p>\n"); } },
  { name: "layout-props", make: app => { basicApp(app); writeFileSync(join(app, "app/layout.view"), "@props(title:text,content:html)\n{{ content }}\n"); } },
  { name: "nested-layout-props", make: app => { basicApp(app); write(join(app, "app/n/layout.view"), "@props(title:text)\n<p></p>\n"); } },
  { name: "reserved-component", make: app => { basicApp(app); write(join(app, "components/Layout.view"), "@props()\n<p></p>\n"); } },
  { name: "not-found-props", make: app => { basicApp(app); write(join(app, "app/not-found.view"), "@props(x:text)\n<p></p>\n"); } },
  { name: "error-view-props", make: app => { basicApp(app); write(join(app, "app/error.view"), "@props(status:int)\n<p></p>\n"); } },
  { name: "metadata-props-key", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'loader="basic_home"\n[metadata.props]\nkeywords="title"\n'); } },
  { name: "metadata-without-head", make: app => { basicApp(app); writeFileSync(join(app, "app/page.toml"), 'loader="basic_home"\n[metadata.props]\ndescription="title"\n'); } },
  { name: "site-url", make: app => basicApp(app, '[site]\nurl="ftp://x.test"\n') },
  { name: "public-symlink", make: app => { basicApp(app); write(join(app, "public/a.txt"), "a"); run(["ln", "-s", "a.txt", join(app, "public/b.txt")]); } },
  { name: "public-reserved", make: app => { basicApp(app); write(join(app, "public/_web/a.txt"), "a"); } },
  { name: "missing-layout", make: app => { basicApp(app); rmSync(join(app, "app/layout.view")); } },
  { name: "no-web-toml", make: app => { basicApp(app); rmSync(join(app, "web.toml")); } },
  { name: "bad-target", make: app => basicApp(app), target: "cloud" },
  { name: "bad-profile", make: app => basicApp(app), profile: "debug" },
  { name: "out-not-empty", make: app => basicApp(app), args: (app, out) => { write(join(out, "keep.txt"), "x"); return [app, out, "all", "release"]; } },
  { name: "out-is-app", make: app => basicApp(app), args: app => [app, app, "all", "release"] },
  { name: "out-in-app", make: app => basicApp(app), args: app => [app, join(app, "dist"), "all", "release"] },
  { name: "paths-fn-mismatch", make: app => { basicApp(app); page(join(app, "app/p/[id]"), 'loader="basic_home"\npaths_fn="bad_paths"\n', "@props(title:text)\n<p></p>\n", 'fn bad_paths() -> Arr Arr u8 _ _ = arr(i in 0..2) if i==0 then "/p/1" else "/q/\\xc3\\xa9"\n'); } },
  { name: "paths-fn-static", make: app => { basicApp(app); page(join(app, "app/p"), 'loader="basic_home"\npaths_fn="basic_home"\n', "@props(title:text)\n<p></p>\n", ""); } },
  { name: "migrate-missing", make: app => basicApp(app), args: (app, out) => ["--migrate", app, out] },
  { name: "migrate-bad-name", make: app => { basicApp(app, '[server]\nmigrate="1x"\n'); write(join(app, "app/migrate.neo"), ""); }, args: (app, out) => ["--migrate", app, out] },
  { name: "bad-neo-home", make: app => basicApp(app), env: { NEO_HOME: "/nonexistent/neo" } },
  { name: "neo-home-not-native", make: app => basicApp(app), env: { NEO_HOME: "~/" } },
  { name: "prepare-off", make: app => basicApp(app), args: () => ["--prepare"], env: { NEOKIT_LIBRARY_CACHE: "off" } },
];

// ---------------------------------------------------------------- main

function setup() {
  mkdirSync(OUT, { recursive: true });
  write(join(NOBROTLI, "brotli.py"), 'raise ImportError("brotli hidden for parity")\n');
  console.log("Building the native driver…");
  const built = run([join(NEO_HOME, "neo"), "build", "tests/cli_golden/build/driver.neo", "-o", DRIVER, "--opt-level", "1"]);
  if (built.status !== 0) { console.error(built.stdout + built.stderr); process.exit(1); }
  kitchenApp(join(APPS, "kitchen"));
  imagesApp(join(APPS, "images"));
}
function applications(): string[] {
  const found: string[] = [];
  const scan = (folder: string, keep: (name: string) => boolean) => {
    for (const name of readdirSync(join(ROOT, folder)).sort())
      if (keep(name) && existsSync(join(ROOT, folder, name, "web.toml"))) found.push(join(ROOT, folder, name));
  };
  scan("examples", () => true);
  scan("tests", name => name.endsWith("_fixture"));
  scan("tests/cli_golden/assets/apps", () => true);
  found.push(join(APPS, "kitchen"), join(APPS, "images"));
  return found;
}
const label = (app: string) => relative(ROOT, app).replace(/^build\/parity\/apps\//, "generated/");

type Row = { app: string; target: string; profile: string; verdict: string; detail: string[]; ms: [number, number] };
// Builds with Python into out, then with the driver into the same path.
function both(args: (out: string) => string[], out: string, prepare?: (out: string) => void) {
  for (const path of [out, out + "-py", out + "-neo"]) rmSync(path, { recursive: true, force: true });
  prepare?.(out);
  const py = python(args(out));
  if (existsSync(out)) renameSync(out, out + "-py");
  prepare?.(out);
  const neo = native(args(out));
  if (existsSync(out)) renameSync(out, out + "-neo");
  return { py, neo };
}
function matrix(): Row[] {
  const rows: Row[] = [];
  const contentTypes: string[] = JSON.parse(run(["python3", "-c", "import json; from compiler.build import CONTENT_TYPES; print(json.dumps(list(CONTENT_TYPES)))"]).stdout);
  const fallback = new Map<string, string>();
  for (const app of applications()) {
    const name = label(app);
    if (only && !only.some(o => name.includes(o))) continue;
    const short = basename(app);
    const cases = targets.map(target => [target, "release"]);
    if (devApps.includes(short)) cases.push(["all", "dev"]);
    for (const [target, profile] of cases) {
      const out = join(OUT, `${short}-${target}-${profile}`);
      const { py, neo } = both(path => [app, path, target, profile], out);
      const detail: string[] = [];
      let verdict: string;
      if (py.status !== 0 || neo.status !== 0) {
        const same = py.status === neo.status && py.stderr === neo.stderr;
        verdict = same ? "identical (both fail)" : "different";
        detail.push(`Python ${py.status}: ${py.stderr.trim().split("\n").slice(-3).join(" | ")}`);
        if (!same) detail.push(`native ${neo.status}: ${neo.stderr.trim().split("\n").slice(-3).join(" | ")}`);
      } else {
        const compared = compareTrees(out + "-py", out + "-neo");
        verdict = compared.result;
        detail.push(...compared.notes, ...compared.problems);
        if (steady(py.stdout) !== steady(neo.stdout)) {
          verdict = "different";
          detail.push(`stdout differs: ${firstDifference(steady(py.stdout), steady(neo.stdout))}`);
        }
        const manifest = JSON.parse(readFileSync(join(out + "-py", "manifest.json"), "utf8"));
        for (const url of Object.keys(manifest.files ?? {})) {
          const suffix = /(\.[^./]*)$/.exec(url.slice(url.lastIndexOf("/")))?.[1]?.toLowerCase() ?? "";
          if (suffix && contentTypes.includes(suffix)) continue;
          const server = readFileSync(join(out + "-py", "generated/server.neo"), "utf8");
          const m = new RegExp(`str_equal\\(request\\.path,${escape(JSON.stringify(url))}\\) then .*?web_reply\\(200,"(?:[^"\\\\]|\\\\.)*","([^"]*)"\\)`).exec(server);
          if (m) fallback.set(`${name}${url}`, m[1]);
        }
      }
      rows.push({ app: name, target, profile, verdict, detail, ms: [py.ms, neo.ms] });
      console.log(`${verdict.padEnd(40)} ${name} ${target} ${profile} (Python ${py.ms}ms, native ${neo.ms}ms)`);
      for (const line of detail) console.log("    " + line);
      if (!flag("--keep") && verdict.startsWith("identical")) for (const side of ["-py", "-neo"]) rmSync(out + side, { recursive: true, force: true });
    }
  }
  if (fallback.size) {
    console.log("\nPublic files typed by the mimetypes fallback (Python's answer, matched by the native build):");
    for (const [url, type] of fallback) console.log(`    ${url}: ${type}`);
  }
  return rows;
}
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function errors(): number {
  let failures = 0;
  console.log("\nError parity:");
  for (const broken of BROKEN) {
    const app = join(APPS, "broken-" + broken.name);
    broken.make(app);
    const out = join(OUT, "broken-" + broken.name);
    const args = (path: string) => broken.args ? broken.args(app, path) : [app, path, broken.target ?? "all", broken.profile ?? "release"];
    for (const path of [out, out + "-py", out + "-neo"]) rmSync(path, { recursive: true, force: true });
    const py = python(args(out), broken.env); rmSync(out, { recursive: true, force: true });
    const neo = native(args(out), broken.env); rmSync(out, { recursive: true, force: true });
    const same = py.status === neo.status && py.stderr === neo.stderr && py.status !== 0;
    if (!same) failures++;
    console.log(`${same ? "same" : "DIFFERENT"}  ${broken.name}: ${py.stderr.trim().split("\n").slice(-1)[0] ?? ""}`);
    if (!same) console.log(`    Python ${py.status}: ${JSON.stringify(py.stderr)}\n    native ${neo.status}: ${JSON.stringify(neo.stderr)}`);
  }
  return failures;
}

// prepare() and refresh_assets(): the same printed lines, files and modes.
function sameTree(left: string, right: string): string[] {
  const problems: string[] = [];
  const a = existsSync(left) ? walk(left, new Set()) : new Map<string, Entry>(), b = existsSync(right) ? walk(right, new Set()) : new Map<string, Entry>();
  for (const path of a.keys()) if (!b.has(path)) problems.push(`only in Python: ${path}`);
  for (const path of b.keys()) if (!a.has(path)) problems.push(`only in native: ${path}`);
  for (const [path, x] of a) {
    const y = b.get(path);
    if (!y) continue;
    if (x.kind !== y.kind || x.mode !== y.mode) problems.push(`${path}: ${x.kind} ${x.mode.toString(8)} against ${y.kind} ${y.mode.toString(8)}`);
    else if (x.kind === "file" && !readFileSync(join(left, path)).equals(readFileSync(join(right, path)))) problems.push(`${path} differs`);
  }
  return problems;
}
function extras(): number {
  let failures = 0;
  const report = (name: string, problems: string[]) => {
    if (problems.length) failures++;
    console.log(`${problems.length ? "DIFFERENT" : "same"}  ${name}`);
    for (const line of problems) console.log("    " + line);
  };
  console.log("\nprepare and refresh_assets:");
  const cache = join(WORK, "prepare-cache");
  const seen: string[] = [];
  for (const side of [python, native]) {
    rmSync(cache, { recursive: true, force: true });
    const ran = side(["--prepare"], { NEOKIT_LIBRARY_CACHE: cache });
    const mode = existsSync(cache) ? (lstatSync(cache).mode & 0o7777).toString(8) : "missing";
    const leftovers = existsSync(cache) ? readdirSync(cache).filter(name => name.startsWith("prepare-")) : [];
    seen.push(JSON.stringify({ status: ran.status, stdout: steady(ran.stdout), stderr: ran.stderr, mode, leftovers }));
  }
  rmSync(cache, { recursive: true, force: true });
  report("prepare", seen[0] === seen[1] ? [] : [`Python ${seen[0]}`, `native ${seen[1]}`]);
  for (const app of [join(ROOT, "examples/studio"), join(APPS, "kitchen")]) {
    const out = join(OUT, "refresh-" + basename(app));
    const { py, neo } = both(path => ["--refresh", app, path], out);
    const problems = sameTree(out + "-py", out + "-neo");
    if (py.status !== neo.status || py.stderr !== neo.stderr) problems.push(`Python ${py.status} ${JSON.stringify(py.stderr)}, native ${neo.status} ${JSON.stringify(neo.stderr)}`);
    if (steady(py.stdout) !== steady(neo.stdout)) problems.push(`stdout: ${firstDifference(steady(py.stdout), steady(neo.stdout))}`);
    report(`refresh_assets ${label(app)}`, problems);
    if (!flag("--keep") && !problems.length) for (const side of ["-py", "-neo"]) rmSync(out + side, { recursive: true, force: true });
  }
  return failures;
}


setup();
const rows = flag("--no-matrix") ? [] : matrix();
const failures = (flag("--no-errors") ? 0 : errors()) + (flag("--no-extras") ? 0 : extras());
const different = rows.filter(row => row.verdict === "different");
console.log(`\n${rows.length} builds: ${rows.filter(r => r.verdict === "identical").length} identical, ` +
  `${rows.filter(r => r.verdict === "identical except documented exceptions").length} identical except documented exceptions, ` +
  `${rows.filter(r => r.verdict === "identical (both fail)").length} failing identically, ${different.length} different; ` +
  `${failures} error, prepare or refresh_assets checks differ.`);
process.exit(different.length || failures ? 1 : 0);
