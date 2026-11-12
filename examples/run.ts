// Build and preview all three showcases; Ctrl-C stops only these servers.
//
//   bun examples/run.ts [--no-build] [--base-port N]
//
// Each server runs in its own session and process group with NEO_THREADS=1,
// logging to build/showcase/NAME.log; build/showcase/previews.json lists their
// URLs and pids. SIGINT or SIGTERM stops them (SIGTERM to each group, SIGKILL
// after 10 s) and exits 0.
import { spawn, type ChildProcess } from "node:child_process";
import { closeSync, existsSync, mkdirSync, openSync, writeFileSync } from "node:fs";
import { createConnection, createServer } from "node:net";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dir, "..");
const APPS = ["ssr-site", "blog", "saas"];
const PROGRAM = "run.ts";
const USAGE = `usage: ${PROGRAM} [-h] [--no-build] [--base-port BASE_PORT]`;
const HELP = `${USAGE}

Build and preview all three showcases; Ctrl-C stops only these servers.

options:
  -h, --help            show this help message and exit
  --no-build
  --base-port BASE_PORT
`;

/** A message for stderr and an exit status. */
class Exit extends Error {
  constructor(message: string, readonly status = 1) { super(message); }
}

function usageError(message: string): never {
  throw new Exit(`${USAGE}\n${PROGRAM}: error: ${message}`, 2);
}

function parse(argv: string[]): { noBuild: boolean; basePort: number } {
  let noBuild = false;
  let basePort = 8111;
  const integer = (text: string) => {
    if (!/^\s*[+-]?\d+(_\d+)*\s*$/.test(text)) usageError(`argument --base-port: invalid int value: '${text}'`);
    return Number(text.trim().replaceAll("_", ""));
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "-h" || arg === "--help") { process.stdout.write(HELP); process.exit(0); }
    else if (arg === "--no-build") noBuild = true;
    else if (arg === "--base-port") {
      if (i + 1 >= argv.length) usageError("argument --base-port: expected one argument");
      basePort = integer(argv[++i]);
    } else if (arg.startsWith("--base-port=")) basePort = integer(arg.slice("--base-port=".length));
    else usageError(`unrecognized arguments: ${argv.slice(i).join(" ")}`);
  }
  if (!(basePort >= 1024 && basePort <= 65533)) usageError("--base-port must be 1024..65533");
  return { noBuild, basePort };
}

/** null when 127.0.0.1:port can be bound, else the error. */
function probe(port: number): Promise<string | null> {
  return new Promise(done => {
    const server = createServer();
    server.once("error", error => done(error.message));
    server.listen({ host: "127.0.0.1", port, exclusive: true }, () => server.close(() => done(null)));
  });
}

/** Whether 127.0.0.1:port accepts a connection within 100 ms. */
function accepts(port: number): Promise<boolean> {
  return new Promise(done => {
    const socket = createConnection({ host: "127.0.0.1", port });
    const finish = (ok: boolean) => { socket.destroy(); done(ok); };
    socket.setTimeout(100, () => finish(false));
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
  });
}

// SIGINT and SIGTERM end the previews: `interrupted` is set and any pause ends early.
let interrupted = false;
let wake = () => {};
for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => { interrupted = true; wake(); });
function pause(ms: number): Promise<void> {
  return new Promise(done => {
    const timer = setTimeout(done, ms);
    wake = () => { clearTimeout(timer); done(); };
  });
}

/** A started process: `started` settles once it runs or failed to start, `exited` once it ended. */
type Child = { process: ChildProcess; started: Promise<void>; exited: Promise<void>; running: () => boolean };
function child(command: string, args: string[], options: Parameters<typeof spawn>[2]): Child {
  const process = spawn(command, args, options);
  let failed = false;
  const failure = new Promise<void>(done => process.once("error", () => { failed = true; done(); }));
  const started = Promise.race([new Promise<void>(done => process.once("spawn", () => done())), failure]);
  const exited = Promise.race([new Promise<void>(done => process.once("exit", () => done())), failure]);
  return { process, started, exited, running: () => !failed && process.exitCode === null && process.signalCode === null };
}
async function within(promise: Promise<void>, ms: number): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise.then(() => true), new Promise<boolean>(done => { timer = setTimeout(() => done(false), ms); })]);
  } finally { clearTimeout(timer); }
}

/** `neokit build` for one showcase, sharing this process's streams and process group.
 *  An interrupt gives it 250 ms to end, then SIGKILL; true when interrupted. */
async function build(name: string, out: string): Promise<boolean> {
  const args = ["build", "--app", join(ROOT, "examples", name), "--out", out];
  const step = child(join(ROOT, "neokit"), args, { cwd: ROOT, stdio: "inherit" });
  await step.started;
  while (step.running() && !interrupted) await Promise.race([step.exited, pause(1000)]);
  if (interrupted) {
    if (!await within(step.exited, 250)) { try { step.process.kill("SIGKILL"); } catch {} await step.exited; }
    return true;
  }
  const status = step.process.exitCode ?? (step.process.signalCode ? `signal ${step.process.signalCode}` : "unknown");
  if (step.process.exitCode !== 0) throw new Exit(`Command '${[join(ROOT, "neokit"), ...args].join(" ")}' returned non-zero exit status ${status}.`);
  return false;
}

async function main(): Promise<void> {
  const args = parse(process.argv.slice(2));
  for (let index = 0; index < 3; index++) {
    const error = await probe(args.basePort + index);
    if (error !== null) throw new Exit(`Preview port ${args.basePort + index} is unavailable; use --base-port. ${error}`);
  }
  const directory = join(ROOT, "build/showcase");
  mkdirSync(directory, { recursive: true });
  const servers: Child[] = [];
  const logs: number[] = [];
  try {
    const info: Record<string, { url: string; pid: number }> = {};
    for (const [index, name] of APPS.entries()) {
      if (interrupted) return;
      if (!args.noBuild && await build(name, join(directory, name))) return;
      const binary = join(directory, name, "server");
      if (!existsSync(binary)) throw new Exit(`Missing ${binary}; run without --no-build first.`);
      const port = args.basePort + index;
      const logPath = join(directory, `${name}.log`);
      const log = openSync(logPath, "w");
      logs.push(log);
      const server = child(binary, [String(port)], { env: { ...process.env, NEO_THREADS: "1" }, stdio: ["inherit", log, log], detached: true });
      servers.push(server);
      await server.started;
      const deadline = performance.now() + 15000;
      for (;;) {
        if (interrupted) return;
        if (!server.running()) throw new Exit(`${name} failed; see ${logPath}`);
        if (await accepts(port)) break;
        if (performance.now() > deadline) throw new Exit(`${name} readiness timeout`);
        await pause(20);
      }
      info[name] = { url: `http://127.0.0.1:${port}`, pid: server.process.pid! };
      console.log(`${name.padEnd(10)} http://127.0.0.1:${port}`);
    }
    writeFileSync(join(directory, "previews.json"), JSON.stringify(info, null, 2) + "\n");
    console.log("Native previews are running. Ctrl-C stops all three.");
    while (!interrupted && servers.every(server => server.running())) await pause(250);
  } finally {
    for (const server of servers) if (server.running()) { try { process.kill(-server.process.pid!, "SIGTERM"); } catch {} }
    for (const server of servers) {
      if (!await within(server.exited, 10000)) { try { process.kill(-server.process.pid!, "SIGKILL"); } catch {} await server.exited; }
    }
    for (const log of logs) closeSync(log);
  }
}

try {
  await main();
} catch (error) {
  if (!(error instanceof Exit)) throw error;
  console.error(error.message);
  process.exit(error.status);
}
process.exit(0);
