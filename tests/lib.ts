// Shared helpers for NeoKit's integration tests: the CLI, supervised processes
// and native servers on free ports, an HTTP/1.1 client modelled on Python's
// http.client that keeps every header line, raw sockets, a Server-Sent Events
// reader, a delayed upstream peer and Python-compatible encoders.
import { expect } from "bun:test";
import { dlopen, ptr, read } from "bun:ffi";
import { spawn, spawnSync, type ChildProcess, type StdioOptions } from "node:child_process";
import { createServer, connect, type Server, type Socket } from "node:net";
import { gunzipSync } from "node:zlib";
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

export const ROOT = resolve(import.meta.dir, "..");
export const sleep = (ms: number) => new Promise<void>(done => setTimeout(done, ms));

export function check(condition: unknown, detail?: unknown): asserts condition {
  if (!condition) throw new Error("check failed" + (detail === undefined ? "" : ": " + (typeof detail === "string" ? detail : JSON.stringify(detail))));
}

/** Run ./neokit with arguments; throws with its output unless `allowFailure`. */
export function neokit(args: string[], options: { cwd?: string; env?: Record<string, string>; allowFailure?: boolean } = {}) {
  const result = spawnSync(join(ROOT, "neokit"), args, { cwd: options.cwd ?? ROOT, env: { ...process.env, ...options.env }, encoding: "utf8", maxBuffer: 64 << 20 });
  if (result.error) throw result.error;
  if (!options.allowFailure && result.status !== 0) throw new Error(`neokit ${args.join(" ")} failed (${result.status}):\n${result.stdout}\n${result.stderr}`);
  return { status: result.status ?? -1, stdout: result.stdout, stderr: result.stderr };
}

/** Run a command and return its stdout; throws on failure. */
export function run(command: string[], options: { cwd?: string; env?: Record<string, string> } = {}) {
  const result = spawnSync(command[0], command.slice(1), { cwd: options.cwd ?? ROOT, env: { ...process.env, ...options.env }, encoding: "utf8", maxBuffer: 64 << 20 });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command.join(" ")} failed (${result.status}):\n${result.stdout}\n${result.stderr}`);
  return result.stdout;
}

/** A temporary directory removed after `body` finishes. */
export async function withTemporary<T>(prefix: string, body: (directory: string) => Promise<T> | T): Promise<T> {
  const directory = mkdtempSync(join(tmpdir(), prefix));
  try { return await body(directory); } finally { rmSync(directory, { recursive: true, force: true }); }
}

/** Write `files` (relative path -> text) under `directory`, creating folders as needed. */
export function writeTree(directory: string, files: Record<string, string>) {
  for (const [name, text] of Object.entries(files)) {
    const path = join(directory, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, text);
  }
}

/** Python's shutil.copy2 for a regular file: contents, mode and timestamps. */
export function copyWithMetadata(source: string, target: string) {
  copyFileSync(source, target);
  const info = statSync(source);
  chmodSync(target, info.mode & 0o7777);
  utimesSync(target, info.atime, info.mtime);
}

/** A port outside the usual ephemeral range, so rapid restarts do not race client allocation or TIME_WAIT. */
export async function freePort(): Promise<number> {
  for (let attempt = 0; attempt < 128; attempt++) {
    const port = 15000 + Math.floor(Math.random() * 15000);
    const free = await new Promise<boolean>(done => {
      const probe = createServer();
      probe.once("error", () => done(false));
      probe.listen(port, "0.0.0.0", () => probe.close(() => done(true)));
    });
    if (free) return port;
  }
  throw new Error("No free local test port");
}

export async function canConnect(port: number): Promise<boolean> {
  return new Promise(done => {
    const socket = connect(port, "127.0.0.1");
    socket.setTimeout(100, () => { socket.destroy(); done(false); });
    socket.once("connect", () => { socket.end(); done(true); });
    socket.once("error", () => done(false));
  });
}

export const gunzip = (data: Uint8Array) => gunzipSync(data);

/** A Neo string literal, as the compiler writes them (for generated test sources). */
export function literal(text: string): string {
  const escapes: Record<number, string> = { 10: "\\n", 13: "\\r", 9: "\\t", 34: '\\"', 92: "\\\\" };
  let out = '"';
  for (const byte of Buffer.from(text, "utf8"))
    out += escapes[byte] ?? (byte >= 32 && byte < 127 ? String.fromCharCode(byte) : "\\x" + byte.toString(16).padStart(2, "0"));
  return out + '"';
}

/** Native binaries import nothing and, on Linux, have no interpreter or shared libraries. */
export function auditBinary(binary: string) {
  check(run(["nm", "-u", binary]).trim() === "", "undefined symbols in " + binary);
  if (process.platform === "linux") {
    check(!run(["readelf", "-l", binary]).includes("INTERP"), "ELF interpreter in " + binary);
    check(!run(["readelf", "-d", binary]).includes("NEEDED"), "shared libraries in " + binary);
  }
}

/** Process ids whose parent is `parent` (Linux /proc). */
export async function childProcesses(parent: number): Promise<number[]> {
  const found: number[] = [];
  for (const name of readdirSync("/proc")) {
    if (!/^\d+$/.test(name)) continue;
    try {
      const stat = readFileSync(`/proc/${name}/stat`, "utf8");
      if (Number(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[1]) === parent) found.push(Number(name));
    } catch {}
  }
  return found;
}

// ---------------------------------------------------------------------------
// Processes

/** True if `promise` settles within `timeout` ms; the timer does not outlive the wait. */
async function within(promise: Promise<unknown>, timeout: number): Promise<boolean> {
  if (timeout === Infinity) { await promise; return true; }
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise.then(() => true), new Promise<boolean>(done => { timer = setTimeout(() => done(false), timeout); })]);
  } finally { clearTimeout(timer); }
}

type Stream = "pipe" | "ignore" | "inherit" | number;

/** A child in its own session and process group (Python's start_new_session=True).
 *  Piped stdout and stderr (by default only stderr) are kept, in arrival order. */
export class Supervised {
  readonly child: ChildProcess;
  private captured = "";
  private failure: Error | undefined;
  private readonly exited: Promise<void>;
  private readonly closed: Promise<void>;
  /** Settles once the process has started, or fails like Python's Popen (missing executable). */
  readonly started: Promise<void>;

  /** `env` is the whole environment (the parent's by default). */
  constructor(command: string, args: string[], options: { env?: Record<string, string | undefined>; cwd?: string; stdin?: "ignore" | "inherit"; stdout?: Stream; stderr?: Stream } = {}) {
    const stdio: StdioOptions = [options.stdin ?? "ignore", options.stdout ?? "ignore", options.stderr ?? "pipe"];
    this.child = spawn(command, args, { detached: true, cwd: options.cwd, env: options.env ?? process.env, stdio });
    for (const stream of [this.child.stdout, this.child.stderr]) {
      stream?.setEncoding("utf8");
      stream?.on("data", chunk => { this.captured += chunk; });
    }
    this.exited = new Promise(done => { this.child.once("exit", () => done()); this.child.once("error", error => { this.failure = error; done(); }); });
    this.closed = new Promise(done => { this.child.once("close", () => done()); this.child.once("error", () => done()); });
    this.started = new Promise((done, fail) => { this.child.once("spawn", () => done()); this.child.once("error", fail); });
    this.started.catch(() => {});
  }

  get pid() { return this.child.pid!; }
  get running() { return this.failure === undefined && this.child.exitCode === null && this.child.signalCode === null; }
  /** Everything read from the piped streams so far. */
  get output() { return this.captured; }

  /** All piped output once the process has exited (waits briefly for the pipes to drain); a spawn failure comes first. */
  async drained(): Promise<string> {
    await within(this.closed, 2000);
    return this.failure ? `${this.failure.message}\n${this.captured}` : this.captured;
  }

  /** The first output line without its newline, like Python's readline() ('' if the process ends first). */
  async firstLine(timeout = 60000): Promise<string> {
    const deadline = performance.now() + timeout;
    for (;;) {
      if (this.captured.includes("\n")) return this.captured.slice(0, this.captured.indexOf("\n"));
      if (this.failure) throw this.failure;
      if (!this.running) { await this.drained(); return this.captured.split("\n")[0]; }
      check(performance.now() < deadline, `no output line within ${timeout} ms`);
      await sleep(5);
    }
  }

  /** True once the process has exited, false if `timeout` ms pass first. */
  async wait(timeout = Infinity): Promise<boolean> {
    if (!this.running) return true;
    return within(this.exited, timeout);
  }

  /** Send `signal` (SIGTERM) to the process group (or, with `group: false`, only the process) and wait
   *  `grace` ms; then SIGKILL the group, and throw if `strict`. */
  async stop(options: { signal?: NodeJS.Signals; group?: boolean; grace: number; strict?: boolean }) {
    const signal = options.signal ?? "SIGTERM";
    if (this.running) { try { process.kill(options.group === false ? this.pid : -this.pid, signal); } catch {} }
    if (await this.wait(options.grace)) return;
    try { process.kill(-this.pid, "SIGKILL"); } catch {}
    await this.wait();
    if (options.strict) throw new Error(`process ${this.pid} was still running ${options.grace} ms after ${signal}`);
  }
}

export type Running = { port: number; process: Supervised; startupMs: number };

/** Run a native server (`binary port ...args`, NEO_THREADS=`workers`, 1 by default) in its own process
 *  group on a free port; wait up to `startup` ms (3 s) until it accepts, run `body`, then SIGTERM the
 *  group. A server still running `stopTimeout` ms (10 s) later is killed and fails the test, like
 *  Python's process.wait(timeout). Its stdout and stderr are printed if anything fails. */
export async function running<T>(binary: string, options: { workers?: number; env?: Record<string, string>; port?: number; args?: string[]; startup?: number; stopTimeout?: number },
                                  body: (server: Running) => Promise<T>): Promise<T> {
  const port = options.port ?? await freePort();
  const began = performance.now();
  const server = new Supervised(binary, [String(port), ...(options.args ?? [])], {
    env: { ...process.env, ...options.env, NEO_THREADS: String(options.workers ?? 1) }, stdout: "pipe", stderr: "pipe",
  });
  let failed = false;
  try {
    const until = began + (options.startup ?? 3000);
    for (;;) {
      if (!server.running) throw new Error("Server exited:\n" + await server.drained());
      if (await canConnect(port)) break;
      if (performance.now() > until) throw new Error(`Server did not start within ${options.startup ?? 3000} ms`);
      await sleep(10);
    }
    return await body({ port, process: server, startupMs: performance.now() - began });
  } catch (error) {
    failed = true;
    throw error;
  } finally {
    try {
      await server.stop({ grace: options.stopTimeout ?? 10000, strict: !failed });
    } catch (error) {
      failed = true;
      throw error;
    } finally {
      if (failed && server.output) console.error("Native server diagnostics:\n" + server.output);
    }
  }
}

/** SIGINT and SIGTERM as an exception (Python's KeyboardInterrupt) for long-running tools. */
export class Interrupted extends Error { constructor() { super("interrupted"); } }
export function interruption(): Promise<never> {
  let fail: (error: Interrupted) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { fail = reject; });
  interrupted.catch(() => {});
  for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => fail(new Interrupted()));
  return interrupted;
}

// ---------------------------------------------------------------------------
// Sockets

/** An error Python would raise as OSError (refused, reset, timed out): callers may retry these. */
export function networkError(code: string, message: string) {
  return Object.assign(new Error(message), { code });
}
export const isNetworkError = (error: unknown) => typeof (error as any)?.code === "string" && /^E[A-Z]+$/.test((error as any).code);

/** Anything with Python-socket-like recv(): a chunk, or an empty buffer once the peer closed. */
export interface Receiver { recv(): Promise<Buffer>; send(data: Uint8Array): Promise<void>; close(): void }

/** A non-blocking socket with blocking-socket semantics: recv() waits up to `timeout` ms for data. */
export class NetSocket implements Receiver {
  private chunks: Buffer[] = [];
  private ended = false;
  private failure: Error | undefined;
  private wake: (() => void) | undefined;
  private constructor(readonly socket: Socket, public timeout: number) {
    socket.on("data", chunk => { this.chunks.push(chunk); this.notify(); });
    socket.on("end", () => { this.ended = true; this.notify(); });
    socket.on("close", () => { this.ended = true; this.notify(); });
    socket.on("error", error => { this.failure = error; this.notify(); });
  }
  private notify() { const wake = this.wake; this.wake = undefined; wake?.(); }

  static async connect(port: number, timeout = 10000): Promise<NetSocket> {
    const peer = new NetSocket(connect(port, "127.0.0.1"), timeout);
    await new Promise<void>((done, fail) => {
      const timer = setTimeout(() => { peer.close(); fail(networkError("ETIMEDOUT", `connect timed out after ${timeout} ms`)); }, timeout);
      peer.socket.once("connect", () => { clearTimeout(timer); done(); });
      peer.socket.once("error", error => { clearTimeout(timer); fail(error); });
    });
    return peer;
  }

  send(data: Uint8Array): Promise<void> {
    return new Promise((done, fail) => this.socket.write(data, error => (error ? fail(error) : done())));
  }

  async recv(): Promise<Buffer> {
    for (;;) {
      if (this.chunks.length) { const data = this.chunks.length === 1 ? this.chunks[0] : Buffer.concat(this.chunks); this.chunks = []; return data; }
      if (this.failure) throw this.failure;
      if (this.ended) return Buffer.alloc(0);
      await new Promise<void>((done, fail) => {
        const timer = setTimeout(() => { this.wake = undefined; fail(networkError("ETIMEDOUT", `read timed out after ${this.timeout} ms`)); }, this.timeout);
        this.wake = () => { clearTimeout(timer); done(); };
      });
    }
  }

  close() { this.socket.destroy(); }
}

// Raw blocking sockets through libc, for what node:net cannot do: a receive
// buffer set before connect (Python's setsockopt(SO_RCVBUF)), and peers that
// never read, so the kernel buffers really fill.
const LINUX = process.platform === "linux";
const C = LINUX
  ? { library: "libc.so.6", errno: "__errno_location", SOL_SOCKET: 1, SO_RCVBUF: 8, SO_RCVTIMEO: 20, SO_SNDTIMEO: 21, SO_NOSIGPIPE: 0, MSG_NOSIGNAL: 0x4000, EINTR: 4, EAGAIN: 11 }
  : { library: "libc.dylib", errno: "__error", SOL_SOCKET: 0xffff, SO_RCVBUF: 0x1002, SO_RCVTIMEO: 0x1006, SO_SNDTIMEO: 0x1005, SO_NOSIGPIPE: 0x1022, MSG_NOSIGNAL: 0, EINTR: 4, EAGAIN: 35 };
let libc: any;
function sys() {
  libc ??= dlopen(C.library, {
    socket: { args: ["i32", "i32", "i32"], returns: "i32" },
    setsockopt: { args: ["i32", "i32", "i32", "ptr", "u32"], returns: "i32" },
    connect: { args: ["i32", "ptr", "u32"], returns: "i32" },
    send: { args: ["i32", "ptr", "u64", "i32"], returns: "i64" },
    recv: { args: ["i32", "ptr", "u64", "i32"], returns: "i64" },
    close: { args: ["i32"], returns: "i32" },
    [C.errno]: { args: [], returns: "ptr" },
  }).symbols;
  return libc;
}
const errno = () => read.i32(sys()[C.errno]());

export class RawSocket implements Receiver {
  private open = true;
  private readonly scratch = Buffer.alloc(65536);
  private constructor(readonly fd: number) {}

  /** Python's socket(); setsockopt(SO_RCVBUF) if asked; connect(); settimeout(timeout). Blocks the event loop briefly. */
  static connect(port: number, options: { receiveBuffer?: number; timeout?: number } = {}): RawSocket {
    const c = sys();
    const fd = c.socket(2, 1, 0);
    if (fd < 0) throw networkError("ESOCKET", `socket() failed, errno ${errno()}`);
    const peer = new RawSocket(fd);
    const option = (name: number, value: Uint8Array) => {
      if (c.setsockopt(fd, C.SOL_SOCKET, name, ptr(value), value.byteLength) !== 0) { const code = errno(); peer.close(); throw networkError("ESOCKOPT", `setsockopt(${name}) failed, errno ${code}`); }
    };
    if (options.receiveBuffer) option(C.SO_RCVBUF, new Uint8Array(new Int32Array([options.receiveBuffer]).buffer));
    if (C.SO_NOSIGPIPE) option(C.SO_NOSIGPIPE, new Uint8Array(new Int32Array([1]).buffer));
    const address = new Uint8Array(16);
    if (LINUX) address[0] = 2; else { address[0] = 16; address[1] = 2; }
    address[2] = port >> 8; address[3] = port & 255; address.set([127, 0, 0, 1], 4);
    if (c.connect(fd, ptr(address), 16) !== 0) { const code = errno(); peer.close(); throw networkError("ECONNREFUSED", `connect() to ${port} failed, errno ${code}`); }
    if (options.timeout) {
      const timeval = new Uint8Array(new BigInt64Array([BigInt(Math.floor(options.timeout / 1000)), BigInt((options.timeout % 1000) * 1000)]).buffer);
      option(C.SO_RCVTIMEO, timeval);
      option(C.SO_SNDTIMEO, timeval);
    }
    return peer;
  }

  /** sendall(). */
  async send(data: Uint8Array) { this.sendAll(data); }
  sendAll(data: Uint8Array) {
    const bytes = Buffer.from(data);
    for (let offset = 0; offset < bytes.length;) {
      const sent = Number(sys().send(this.fd, ptr(bytes, offset), bytes.length - offset, C.MSG_NOSIGNAL));
      if (sent < 0) { const code = errno(); if (code === C.EINTR) continue; throw networkError(code === C.EAGAIN ? "ETIMEDOUT" : "ESEND", `send() failed, errno ${code}`); }
      offset += sent;
    }
  }

  /** recv(65536): up to 64 KiB, or an empty buffer once the peer closed. */
  async recv(): Promise<Buffer> {
    for (;;) {
      const received = Number(sys().recv(this.fd, ptr(this.scratch), this.scratch.length, 0));
      if (received >= 0) return Buffer.from(this.scratch.subarray(0, received));
      const code = errno();
      if (code !== C.EINTR) throw networkError(code === C.EAGAIN ? "ETIMEDOUT" : "ERECV", `recv() failed, errno ${code}`);
    }
  }

  close() { if (this.open) { this.open = false; sys().close(this.fd); } }
}

/** Write raw bytes and return everything the server sends until it closes. */
export function exchange(port: number, data: Uint8Array, timeout = 10000): Promise<Buffer> {
  return new Promise((done, fail) => {
    const chunks: Buffer[] = [];
    const socket: Socket = connect(port, "127.0.0.1", () => socket.write(data));
    socket.setTimeout(timeout, () => { socket.destroy(); fail(new Error(`timed out after ${timeout} ms`)); });
    socket.on("data", chunk => chunks.push(chunk));
    socket.on("end", () => done(Buffer.concat(chunks)));
    socket.on("error", error => (chunks.length ? done(Buffer.concat(chunks)) : fail(error)));
  });
}

/** Write raw bytes and return what has arrived once `until` accepts it (or the server closes). */
export function firstBytes(port: number, data: Uint8Array, until: (received: Buffer) => boolean, timeout = 10000): Promise<Buffer> {
  return new Promise((done, fail) => {
    let received = Buffer.alloc(0);
    const socket: Socket = connect(port, "127.0.0.1", () => socket.write(data));
    const finish = () => { socket.destroy(); done(received); };
    socket.setTimeout(timeout, () => { socket.destroy(); fail(new Error(`timed out after ${timeout} ms`)); });
    socket.on("data", chunk => { received = Buffer.concat([received, chunk]); if (until(received)) finish(); });
    socket.on("end", finish);
    socket.on("error", error => (received.length ? finish() : fail(error)));
  });
}

// ---------------------------------------------------------------------------
// HTTP/1.1

/** A response; header lookups are case-insensitive and keep repeated fields. */
export class Reply {
  constructor(readonly status: number, readonly fields: [string, string][], readonly body: Buffer, readonly statusLine: string,
              /** Milliseconds from the request until its response head, and until its whole body (request() only). */
              readonly ttfb = 0, readonly total = 0) {}
  /** The first value of a header, or undefined. */
  header(name: string) { return this.all(name)[0]; }
  /** The last value of a header, as Python's dict(response.getheaders()) keeps it. */
  last(name: string) { return this.all(name).at(-1); }
  /** Every value of a header, in order. */
  all(name: string) { const key = name.toLowerCase(); return this.fields.filter(([field]) => field.toLowerCase() === key).map(([, value]) => value); }
  has(name: string) { return this.header(name) !== undefined; }
  get text() { return this.body.toString("utf8"); }
  json() { return JSON.parse(this.text); }
}

const incomplete = (what: string) => new Error("IncompleteRead: connection closed " + what);

/** Status line and fields of a response head (without its blank line). */
function parseHead(head: Buffer) {
  const lines = head.toString("latin1").split("\r\n");
  const statusLine = lines[0];
  const match = /^HTTP\/\d\.\d (\d{3})(?: .*)?$/.exec(statusLine);
  if (!match) throw new Error("BadStatusLine: " + JSON.stringify(statusLine));
  const fields = lines.slice(1).map(line => { const colon = line.indexOf(":"); return [line.slice(0, colon), line.slice(colon + 1).trim()] as [string, string]; });
  return { status: Number(match[1]), statusLine, fields };
}

/** One chunk-size line: Python's int(line.split(';')[0], 16). */
function chunkSize(line: Buffer) {
  const text = line.toString("latin1").split(";")[0].trim();
  if (!/^[0-9a-fA-F]+$/.test(text)) throw new Error("bad chunk size " + JSON.stringify(text));
  return parseInt(text, 16);
}

/** The response at the start of `data`, framed as http.client reads it: no body for HEAD, 1xx, 204 and
 *  304; chunked; Content-Length; otherwise everything until the close. Undefined while more bytes are
 *  needed; once the peer has `closed`, a truncated response throws instead. */
function frame(data: Buffer, method: string, closed: boolean) {
  const headEnd = data.indexOf("\r\n\r\n");
  if (headEnd < 0) {
    if (closed) throw networkError("ECONNRESET", "RemoteDisconnected: closed before the response head");
    return undefined;
  }
  const { status, statusLine, fields } = parseHead(data.subarray(0, headEnd));
  const find = (name: string) => fields.find(([field]) => field.toLowerCase() === name)?.[1];
  const framed = (body: Buffer, end: number) => ({ status, statusLine, fields, body, end });
  const short = (what: string) => { if (closed) throw incomplete(what); return undefined; };
  let offset = headEnd + 4;
  if (method === "HEAD" || status === 204 || status === 304 || (status >= 100 && status < 200)) return framed(Buffer.alloc(0), offset);
  if ((find("transfer-encoding") ?? "").toLowerCase() === "chunked") {
    const parts: Buffer[] = [];
    for (;;) {
      const line = data.indexOf("\n", offset);
      if (line < 0) return short("before a chunk size");
      const size = chunkSize(data.subarray(offset, line + 1));
      offset = line + 1;
      if (size === 0) {
        // Trailer lines, up to an empty line or the close.
        for (;;) {
          const end = data.indexOf("\n", offset);
          if (end < 0) return closed ? framed(Buffer.concat(parts), data.length) : undefined;
          const trailer = data.subarray(offset, end + 1).toString("latin1");
          offset = end + 1;
          if (trailer.trim() === "") return framed(Buffer.concat(parts), offset);
        }
      }
      if (offset + size + 2 > data.length) return short("inside a chunk");
      parts.push(data.subarray(offset, offset + size));
      offset += size + 2;
    }
  }
  const declared = find("content-length");
  if (declared !== undefined && /^\d+$/.test(declared)) {
    const length = Number(declared);
    if (offset + length > data.length) return short(`after ${data.length - offset} of ${length} body bytes`);
    return framed(data.subarray(offset, offset + length), offset + length);
  }
  return closed ? framed(data.subarray(offset), data.length) : undefined;
}

/** Parse consecutive responses from a closed connection's bytes; `methods` gives each request's method. */
export function parseResponses(raw: Buffer, methods: string[]): Reply[] {
  const replies: Reply[] = [];
  let offset = 0;
  for (const method of methods) {
    if (offset >= raw.length) break;
    const response = frame(raw.subarray(offset), method, true)!;
    replies.push(new Reply(response.status, response.fields, Buffer.from(response.body), response.statusLine));
    offset += response.end;
  }
  return replies;
}

/** One client connection, read like Python's http.client: write requests, then read each reply by its framing. */
export class Connection {
  /** Bytes received but not yet consumed. */
  buffer = Buffer.alloc(0);
  private ended = false;
  private constructor(readonly socket: NetSocket) {}

  /** `timeout` applies to the connect and to each read, like a socket timeout. */
  static async open(port: number, timeout = 10000): Promise<Connection> {
    return new Connection(await NetSocket.connect(port, timeout));
  }

  write(data: string | Uint8Array): Promise<void> {
    return this.socket.send(typeof data === "string" ? Buffer.from(data) : data);
  }

  /** Read one more chunk; false once the peer has closed. */
  async fill(): Promise<boolean> {
    if (this.ended) return false;
    const chunk = await this.socket.recv();
    if (!chunk.length) { this.ended = true; return false; }
    this.buffer = this.buffer.length ? Buffer.concat([this.buffer, chunk]) : chunk;
    return true;
  }

  /** Bytes up to and including `delimiter`, or null if the peer closed first. */
  async until(delimiter: string): Promise<Buffer | null> {
    let from = 0;
    for (;;) {
      const at = this.buffer.indexOf(delimiter, from, "latin1");
      if (at >= 0) return this.take(at + delimiter.length);
      from = Math.max(0, this.buffer.length - delimiter.length + 1);
      if (!await this.fill()) return null;
    }
  }

  /** Exactly `count` bytes, or null if the peer closed first. */
  async exactly(count: number): Promise<Buffer | null> {
    while (this.buffer.length < count) if (!await this.fill()) return null;
    return this.take(count);
  }

  take(count: number) { const out = this.buffer.subarray(0, count); this.buffer = this.buffer.subarray(count); return out; }

  /** The next response, skipping 100 Continue; `method` tells whether it has a body. */
  async reply(method = "GET", started = performance.now()): Promise<Reply> {
    let ttfb: number | undefined;
    for (;;) {
      if (ttfb === undefined && this.buffer.includes("\r\n\r\n")) ttfb = performance.now() - started;
      const response = frame(this.buffer, method, this.ended);
      if (response) {
        this.buffer = this.buffer.subarray(response.end);
        if (response.status === 100) { ttfb = undefined; continue; }
        return new Reply(response.status, response.fields, Buffer.from(response.body), response.statusLine, ttfb ?? 0, performance.now() - started);
      }
      await this.fill();
    }
  }

  /** Everything until the peer closes; a reset after the last reply counts as the close. */
  async rest(): Promise<Buffer> {
    try {
      while (await this.fill());
    } catch (error) {
      if ((error as any)?.code !== "ECONNRESET") throw error;
    }
    return this.take(this.buffer.length);
  }

  close() { this.socket.close(); }
}

export type RequestOptions = {
  method?: string;
  headers?: Record<string, string>;
  body?: string | Uint8Array;
  /** Connect and per-read timeout in ms (10 s), like a socket timeout. */
  timeout?: number;
  /** Send no Connection header and hang up after reading the one reply, as Python's http.client does.
   *  Otherwise the request says Connection: close, and the server must then close with nothing after the reply. */
  keepAlive?: boolean;
  /** A reply that may have a body (not to HEAD, not 204 or 304) must declare a Content-Length equal to the
   *  body's length. */
  requireLength?: boolean;
};

/** One request on its own connection, sent as Python's http.client sends it: Host and
 *  Accept-Encoding: identity unless given, then Content-Length for a body (0 for a bodiless POST, PUT
 *  or PATCH) and the caller's headers. The reply is read by its framing; see RequestOptions for
 *  Connection: close and Content-Length strictness. Use reply.last() for Python's dict(getheaders()). */
export async function request(port: number, path = "/", options: RequestOptions = {}): Promise<Reply> {
  const method = options.method ?? "GET";
  const headers = options.headers ?? {};
  const names = new Set(Object.keys(headers).map(name => name.toLowerCase()));
  const body = options.body === undefined ? undefined : Buffer.from(options.body);
  let head = `${method} ${path} HTTP/1.1\r\n`;
  if (!names.has("host")) head += `Host: 127.0.0.1:${port}\r\n`;
  if (!names.has("accept-encoding")) head += "Accept-Encoding: identity\r\n";
  if (!names.has("content-length") && !names.has("transfer-encoding")) {
    if (body !== undefined) head += `Content-Length: ${body.length}\r\n`;
    else if (["POST", "PUT", "PATCH"].includes(method.toUpperCase())) head += "Content-Length: 0\r\n";
  }
  for (const [name, value] of Object.entries(headers)) head += `${name}: ${value}\r\n`;
  if (!options.keepAlive) head += "Connection: close\r\n";
  head += "\r\n";
  const started = performance.now();
  const connection = await Connection.open(port, options.timeout ?? 10000);
  try {
    await connection.write(body ? Buffer.concat([Buffer.from(head, "latin1"), body]) : Buffer.from(head, "latin1"));
    const reply = await connection.reply(method, started);
    if (!options.keepAlive) {
      const after = await connection.rest();
      check(after.length === 0, `${method} ${path}: ${after.length} bytes after the reply to Connection: close`);
    }
    if (options.requireLength && method !== "HEAD" && reply.status !== 204 && reply.status !== 304) {
      check(reply.has("Content-Length"), `${method} ${path}: no Content-Length`);
      check(Number(reply.header("Content-Length")) === reply.body.length, `${method} ${path}: Content-Length ${reply.header("Content-Length")} but ${reply.body.length} bytes`);
    }
    return reply;
  } finally { connection.close(); }
}

// ---------------------------------------------------------------------------
// Server-Sent Events and the realtime fixtures (tests/realtime_fixture, tests/realtime_limits_fixture)

export type ServerEvent = { event?: string; id?: string; data?: any; comment?: string };
const utf8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

/** A GET of an event stream (by default the realtime fixture's /feed with
 *  its session cookie), checked for SSE headers, read line by line from the de-chunked body.
 *  `timeout` is the per-read socket timeout. */
export class EventStream {
  private decoded = Buffer.alloc(0);
  private remaining = 0;
  private finished = false;
  private constructor(readonly connection: Connection, readonly reply: Reply) {}

  static async open(port: number, path = "/feed", headers: Record<string, string> = {}, timeout = 5000): Promise<EventStream> {
    const connection = await Connection.open(port, timeout);
    try {
      const fields = { Host: `127.0.0.1:${port}`, "Accept-Encoding": "identity", Cookie: "session=allowed", ...headers };
      await connection.write(Buffer.from(`GET ${path} HTTP/1.1\r\n` + Object.entries(fields).map(([name, value]) => `${name}: ${value}\r\n`).join("") + "\r\n", "latin1"));
      const raw = await connection.until("\r\n\r\n");
      if (!raw) throw networkError("ECONNRESET", "RemoteDisconnected: closed before the response head");
      const { status, statusLine, fields: received } = parseHead(raw.subarray(0, raw.length - 4));
      const reply = new Reply(status, received, Buffer.alloc(0), statusLine);
      expect(reply.status).toBe(200);
      expect(reply.all("Content-Type").join(", ")).toBe("text/event-stream; charset=utf-8");
      expect([reply.all("Transfer-Encoding").join(", "), reply.has("Content-Length")]).toEqual(["chunked", false]);
      expect([reply.all("X-Accel-Buffering").join(", "), reply.all("Cache-Control").join(", ")]).toEqual(["no", "no-store"]);
      return new EventStream(connection, reply);
    } catch (error) { connection.close(); throw error; }
  }

  /** Decode more of the chunked body; false at its end. */
  private async pump(): Promise<boolean> {
    const stream = this.connection;
    for (;;) {
      if (this.finished) return false;
      if (this.remaining > 0) {
        if (!stream.buffer.length && !await stream.fill()) throw incomplete("inside a chunk");
        const piece = stream.take(Math.min(this.remaining, stream.buffer.length));
        this.decoded = this.decoded.length ? Buffer.concat([this.decoded, piece]) : Buffer.from(piece);
        this.remaining -= piece.length;
        if (this.remaining === 0 && !await stream.exactly(2)) throw incomplete("after a chunk");
        return true;
      }
      const line = await stream.until("\n");
      if (!line) throw incomplete("before a chunk size");
      const size = chunkSize(line);
      if (size === 0) {
        this.finished = true;
        for (;;) { const trailer = await stream.until("\n"); if (!trailer || trailer.toString("latin1").trim() === "") break; }
        return false;
      }
      this.remaining = size;
    }
  }

  /** One line including its newline; an empty buffer at the end of the stream. */
  async readline(): Promise<Buffer> {
    for (;;) {
      const at = this.decoded.indexOf(10);
      if (at >= 0) { const line = this.decoded.subarray(0, at + 1); this.decoded = this.decoded.subarray(at + 1); return line; }
      if (!await this.pump()) { const rest = this.decoded; this.decoded = Buffer.alloc(0); return rest; }
    }
  }

  /** The next event with data (or, with `comments`, the next comment line). */
  async next(comments = false): Promise<ServerEvent> {
    let event: ServerEvent = {};
    const data: string[] = [];
    for (;;) {
      const raw = await this.readline();
      if (!raw.length) throw new Error("EOFError: Event stream closed");
      const line = utf8.decode(raw).replace(/[\r\n]+$/, "");
      if (line.startsWith(":") && comments) return { comment: line };
      if (!line) {
        if (data.length) { event.data = JSON.parse(data.join("\n")); return event; }
        event = {};
        continue;
      }
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") data.push(value);
      else if (field === "event" || field === "id") event[field] = value;
    }
  }

  /** The next application event ("message" or "dashboard"). */
  async message(): Promise<ServerEvent> {
    for (;;) {
      const event = await this.next();
      if (event.event === "message" || event.event === "dashboard") return event;
    }
  }

  close() { this.connection.close(); }
}

/** GET /stats from a realtime fixture. */
export const stats = async (port: number) => (await request(port, "/stats", { keepAlive: true, timeout: 5000 })).json();

/** Wait up to 2 s until a realtime fixture reports no subscribers, tasks or captures. */
export async function quiet(port: number) {
  const until = performance.now() + 2000;
  let current: any;
  while (performance.now() < until) {
    current = await stats(port);
    if (["subscribers", "tasks", "captures"].every(key => current[key] === 0)) return current;
    await sleep(10);
  }
  throw new Error("Realtime state did not settle: " + JSON.stringify(current));
}

// ---------------------------------------------------------------------------
// A delayed HTTP/1.0 upstream for the async features (NEOKIT_ASYNC_HTTP_PORT)

/** A local HTTP/1.0 peer: GET /name answers "Network <name> café" after delays[path]
 *  seconds (8 by default) and counts requests whose client closes the connection first. */
export class DelayedPeer {
  cancelled = 0;
  private readonly sockets = new Set<Socket>();
  private readonly timers = new Set<ReturnType<typeof setTimeout>>();
  private constructor(private readonly server: Server, readonly port: number, public delays: Record<string, number>) {}

  static async start(delays: Record<string, number> = { "/auth": 0.03, "/fast": 0.08, "/slow": 1.2 }): Promise<DelayedPeer> {
    const server = createServer();
    await new Promise<void>((done, fail) => { server.once("error", fail); server.listen({ port: 0, host: "127.0.0.1", backlog: 512 }, () => done()); });
    const peer = new DelayedPeer(server, (server.address() as { port: number }).port, delays);
    server.on("connection", socket => peer.serve(socket));
    return peer;
  }

  private serve(socket: Socket) {
    this.sockets.add(socket);
    socket.once("close", () => this.sockets.delete(socket));
    socket.on("error", () => socket.destroy());
    let head = Buffer.alloc(0), state: "head" | "waiting" | "done" = "head";
    let timer: ReturnType<typeof setTimeout> | undefined;
    socket.on("data", chunk => {
      if (state !== "head") return;
      head = Buffer.concat([head, chunk]);
      const end = head.indexOf("\r\n\r\n");
      if (end < 0) { if (head.length > 65536) socket.destroy(); return; }
      const [method, path = "/"] = head.subarray(0, end).toString("latin1").split("\r\n")[0].split(" ");
      if (method !== "GET") { state = "done"; socket.end("HTTP/1.0 501 Unsupported method\r\nContent-Length: 0\r\n\r\n"); return; }
      state = "waiting";
      timer = setTimeout(() => {
        this.timers.delete(timer!);
        if (state !== "waiting") return;
        state = "done";
        const body = Buffer.from(`Network <${path.slice(1)}> café`);
        const reply = `HTTP/1.0 200 OK\r\nServer: neokit-test-peer\r\nDate: ${new Date().toUTCString()}\r\nContent-Length: ${body.length}\r\n\r\n`;
        socket.end(Buffer.concat([Buffer.from(reply, "latin1"), body]));
      }, (this.delays[path] ?? 8) * 1000);
      this.timers.add(timer);
    });
    // The client closed while its answer was still pending.
    socket.on("end", () => {
      if (state !== "waiting") return;
      state = "done";
      clearTimeout(timer);
      this.timers.delete(timer!);
      this.cancelled++;
      socket.destroy();
    });
  }

  async close() {
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    const closed = new Promise<void>(done => this.server.close(() => done()));
    for (const socket of this.sockets) socket.destroy();
    await closed;
  }
}

// ---------------------------------------------------------------------------
// Python-compatible encoders

/** Python's json.dumps: ", " and ": " separators, or `indent` spaces per level; ASCII-only unless
 *  `ensureAscii` is false. */
export function pythonJson(value: unknown, options: { indent?: number; ensureAscii?: boolean } = {}, level = 0): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") {
    const text = JSON.stringify(value);
    return options.ensureAscii === false ? text : text.replace(/[\u007f-￿]/g, c => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
  }
  if (typeof value !== "object") return JSON.stringify(value);
  const entries = Array.isArray(value) ? value.map(item => pythonJson(item, options, level + 1))
    : Object.entries(value as object).map(([key, item]) => pythonJson(key, options) + ": " + pythonJson(item, options, level + 1));
  const [open, close] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  if (!entries.length) return open + close;
  if (options.indent === undefined) return open + entries.join(", ") + close;
  const inner = "\n" + " ".repeat(options.indent * (level + 1)), outer = "\n" + " ".repeat(options.indent * level);
  return open + inner + entries.join("," + inner) + outer + close;
}

/** Python's int() for decimal text: surrounding whitespace, a sign and digit-group underscores; anything else throws. */
export function pythonInt(text: string | Buffer): number {
  const value = (typeof text === "string" ? text : text.toString("latin1")).replace(/^(?:(?!﻿)[\s\x1c-\x1f\x85])+|(?:(?!﻿)[\s\x1c-\x1f\x85])+$/g, "");
  if (!/^[+-]?\d+(?:_\d+)*$/.test(value)) throw new Error(`invalid literal for int(): ${JSON.stringify(value)}`);
  return Number(value.replace(/_/g, ""));
}

/** Python's urlencode() (quote_plus): spaces become '+', and only letters, digits and _.-~ stay literal. */
export function urlencode(fields: Record<string, string | number>) {
  const quote = (text: string) => Array.from(Buffer.from(text, "utf8"), byte => {
    const char = String.fromCharCode(byte);
    if (/[A-Za-z0-9_.\-~]/.test(char)) return char;
    return byte === 32 ? "+" : "%" + byte.toString(16).toUpperCase().padStart(2, "0");
  }).join("");
  return Object.entries(fields).map(([key, value]) => quote(key) + "=" + quote(String(value))).join("&");
}

/** Non-overlapping occurrences, like Python's str.count() and bytes.count(). */
export function countOf(haystack: Buffer | string, needle: string) {
  const data = typeof haystack === "string" ? Buffer.from(haystack) : haystack;
  let count = 0;
  for (let at = data.indexOf(needle); at >= 0; at = data.indexOf(needle, at + Buffer.byteLength(needle))) count++;
  return count;
}
