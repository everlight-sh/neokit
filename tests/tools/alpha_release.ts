// Exercise the release archive without a checkout, package cache or global SDK.
//
//   bun tests/tools/alpha_release.ts --archive neokit-VERSION.tar.gz [--sha256 HEX]
//
// In a new temporary directory, with only PATH, HOME (a new one), LC_ALL=C and
// NEO_THREADS=1 in the environment: unpack the archive, run its install.sh,
// remove the download, run `neokit doctor` and `neokit init --standalone`,
// remove the installation, move the project, check that every neo.lock package
// lies inside it, then build, serve (the starter page and its browser asset)
// and export it. --sha256 first checks the archive's SHA-256.
//
// unpack() is also what .github/workflows/verify-alpha.yml uses to find the
// SDK's framework for tests/tools/alpha_https.ts.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { parseArgs } from "node:util";
import { gunzipSync } from "node:zlib";
import { check, freePort, isNetworkError, request, sleep } from "../lib";

type Member = { name: string; type: string; mode: number; data: Uint8Array };

/** The NUL-terminated text of a header field. */
function field(block: Uint8Array, start: number, length: number): string {
  const bytes = block.subarray(start, start + length);
  const end = bytes.indexOf(0);
  return Buffer.from(end < 0 ? bytes : bytes.subarray(0, end)).toString("utf8");
}

/** An octal number field (spaces and NULs trimmed, empty is 0), or a base-256 one. */
function number(block: Uint8Array, start: number, length: number): number {
  if (block[start] & 0x80) {
    let value = block[start] & 0x3f;
    for (let i = start + 1; i < start + length; i++) value = value * 256 + block[i];
    return value;
  }
  const text = Buffer.from(block.subarray(start, start + length)).toString("latin1").replace(/[\0 ]+$/, "").replace(/^[\0 ]+/, "");
  if (text === "") return 0;
  if (!/^[0-7]+$/.test(text)) throw new Error(`invalid number field ${JSON.stringify(text)}`);
  return parseInt(text, 8);
}

/** pax extended header records: "LENGTH key=value\n". */
function paxRecords(data: Uint8Array): Map<string, string> {
  const records = new Map<string, string>();
  const text = Buffer.from(data);
  let at = 0;
  while (at < text.length) {
    const space = text.indexOf(0x20, at);
    if (space < 0) break;
    const length = Number(text.subarray(at, space).toString("latin1"));
    if (!Number.isInteger(length) || length <= 0) throw new Error("invalid pax header");
    const record = text.subarray(space + 1, at + length - 1).toString("utf8");
    const equals = record.indexOf("=");
    if (equals > 0) records.set(record.slice(0, equals), record.slice(equals + 1));
    at += length;
  }
  return records;
}

/** The members of a tar archive (gzip-compressed or plain), with pax and GNU long names applied. */
export function tarMembers(archive: Uint8Array): Member[] {
  const tar = archive[0] === 0x1f && archive[1] === 0x8b ? gunzipSync(archive) : archive;
  const members: Member[] = [];
  let longName: string | undefined;
  let pax = new Map<string, string>();
  for (let at = 0; at + 512 <= tar.length;) {
    const block = tar.subarray(at, at + 512);
    if (block.every(byte => byte === 0)) break;
    const stored = number(block, 148, 8);
    let sum = 8 * 0x20;
    for (let i = 0; i < 512; i++) if (i < 148 || i >= 156) sum += block[i];
    if (sum !== stored) throw new Error(`bad checksum in the tar header at offset ${at}`);
    const type = String.fromCharCode(block[156] || 0x30);
    const size = pax.has("size") ? Number(pax.get("size")) : number(block, 124, 12);
    const data = tar.subarray(at + 512, at + 512 + size);
    at += 512 + Math.ceil(size / 512) * 512;
    if (type === "x") { pax = paxRecords(data); continue; }
    if (type === "g") continue;
    if (type === "L") { longName = field(data, 0, data.length); continue; }
    const prefix = Buffer.from(block.subarray(257, 265)).toString("latin1") === "ustar\x0000" ? field(block, 345, 155) : "";
    const name = pax.get("path") ?? longName ?? (prefix ? `${prefix}/${field(block, 0, 100)}` : field(block, 0, 100));
    members.push({ name, type, mode: number(block, 100, 8), data });
    longName = undefined;
    pax = new Map();
  }
  return members;
}

/** Extract the SDK archive into destination: regular files only, every path relative and without
 *  "..". Returns the single top directory, which must hold MANIFEST.json. */
export function unpack(archive: string, destination: string): string {
  for (const member of tarMembers(readFileSync(archive))) {
    const parts = member.name.split("/");
    check(["0", "7"].includes(member.type) && !isAbsolute(member.name) && !parts.includes(".."), member.name);
    const path = join(destination, member.name);
    mkdirSync(resolve(path, ".."), { recursive: true });
    writeFileSync(path, member.data);
    chmodSync(path, member.mode & 0o7777);
  }
  const roots = readdirSync(destination).map(name => join(destination, name));
  check(roots.length === 1 && statSync(join(roots[0], "MANIFEST.json"), { throwIfNoEntry: false })?.isFile(), roots);
  return roots[0];
}

/** Run a command sharing this process's streams; throws unless it exits 0. */
function call(command: string[], env: Record<string, string>, cwd: string) {
  const result = spawnSync(command[0], command.slice(1), { env, cwd, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Command ${JSON.stringify(command)} returned non-zero exit status ${result.status ?? result.signal}.`);
}

/** GET path on its own connection (Connection: close, 2 s timeout). */
async function get(port: number, path: string) {
  const reply = await request(port, path, { timeout: 2000 });
  return { status: reply.status, body: reply.body };
}

async function checkArchive(archive: string) {
  const temporary = mkdtempSync(join(tmpdir(), "neokit-alpha-clean-"));
  try {
    const root = realpathSync(temporary);
    const home = join(root, "home");
    mkdirSync(home);
    const env = { PATH: process.env.PATH ?? "", HOME: home, LC_ALL: "C", NEO_THREADS: "1" };
    const source = unpack(archive, join(root, "download"));
    const prefix = join(root, "installed tools");
    call([join(source, "install.sh"), prefix], env, root);
    renameSync(source, join(root, "removed-download"));
    const neokit = join(prefix, "bin/neokit");
    call([neokit, "doctor"], env, root);
    const project = join(root, "first-app");
    call([neokit, "init", "--standalone", project], env, root);
    renameSync(prefix, join(root, "removed-installation"));
    const moved = join(root, "relocated project");
    renameSync(project, moved);
    const lock = JSON.parse(readFileSync(join(moved, "neo.lock"), "utf8"));
    for (const pkg of Object.values<{ path: string }>(lock.packages)) {
      const inside = relative(moved, realpathSync(join(moved, pkg.path)));
      check(inside === "" || (!inside.startsWith(".." + sep) && inside !== ".." && !isAbsolute(inside)), pkg.path);
    }
    call(["./neokit", "build"], env, moved);
    const port = await freePort();
    const server = spawn("./neokit", ["serve", "--no-build", "--port", String(port)], { env, cwd: moved, stdio: "inherit", detached: true });
    const exited = new Promise<number | null>(done => server.once("exit", code => done(code)));
    const running = () => server.exitCode === null && server.signalCode === null;
    let exitCode: number | null = null;
    try {
      let page: { status: number; body: Buffer } | undefined;
      for (let attempt = 0; attempt < 100 && !page; attempt++) {
        check(running(), "the starter server exited");
        try { page = await get(port, "/"); } catch (error) { if (!isNetworkError(error)) throw error; await sleep(50); }
      }
      if (!page) throw new Error("Starter server did not listen");
      check(page.status === 200 && page.body.includes("<h1>Hello from NeoKit</h1>"), page.status);
      const manifest = JSON.parse(readFileSync(join(moved, ".neokit/build/manifest.json"), "utf8"));
      const asset: string = manifest.assets["client.js"];
      check(page.body.includes(asset) && (await get(port, asset)).status === 200, asset);
    } finally {
      if (running()) { try { process.kill(-server.pid!, "SIGTERM"); } catch {} }
      const stopped = await Promise.race([exited.then(() => true), sleep(35000).then(() => false)]);
      if (!stopped) {
        try { process.kill(-server.pid!, "SIGKILL"); } catch {}
        await exited;
        throw new Error("the starter server was still running 35 s after SIGTERM");
      }
      exitCode = server.exitCode;
    }
    check(exitCode === 0, `the starter server exited with ${exitCode ?? server.signalCode}`);
    call(["./neokit", "export"], env, moved);
    check(readFileSync(join(root, "relocated project-export/index.html")).includes("<h1>Hello from NeoKit</h1>"));
    console.log("PASS cold install, moved standalone starter, native SSR, browser asset and static export");
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
}

if (import.meta.main) {
  const usage = "usage: alpha_release.ts [-h] --archive ARCHIVE [--sha256 SHA256]";
  let values: { archive?: string; sha256?: string; help?: boolean };
  try {
    ({ values } = parseArgs({ options: { archive: { type: "string" }, sha256: { type: "string" }, help: { type: "boolean", short: "h" } } }));
    if (values.help) { console.log(`${usage}\n\nExercise the release archive without a checkout, package cache or global SDK.`); process.exit(0); }
    if (!values.archive) throw new Error("the following arguments are required: --archive");
  } catch (error) {
    console.error(`${usage}\nalpha_release.ts: error: ${(error as Error).message}`);
    process.exit(2);
  }
  const archive = resolve(values.archive);
  if (values.sha256) check(createHash("sha256").update(readFileSync(archive)).digest("hex") === values.sha256.toLowerCase(), "the archive's SHA-256 does not match --sha256");
  await checkArchive(archive);
}
