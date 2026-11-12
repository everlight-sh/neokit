// Verify the Meridian Compose deployment with a trusted local test CA.
//
//   bun tests/tools/alpha_https.ts --framework SDK/framework --out result.json [--http-port 8189] [--https-port 8449]
//
// Starts deploy/meridian.compose.yaml with deploy/meridian.local.compose.yaml
// (Caddy's internal CA, DOMAIN=localhost, bound to 127.0.0.1) under a new
// project name, trusts only that CA, and checks over HTTPS: the health check,
// the Meridian page and customer directory, the proxy refusing mutations, SSE
// replay with Last-Event-ID, a non-root read-only app container without
// published ports, a graceful stop with exit status 0 within 37 s, and a
// restart without rebuilding. Writes the results to --out as JSON and always
// takes the project down (with its volumes).
import { spawnSync, type SpawnSyncOptions } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { request as httpsRequest } from "node:https";
import type { ClientRequest, IncomingMessage } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";

function check(condition: unknown, detail?: unknown): asserts condition {
  if (!condition) throw new Error("check failed" + (detail === undefined ? "" : ": " + JSON.stringify(detail)));
}
const sleep = (ms: number) => new Promise<void>(done => setTimeout(done, ms));

/** A response read line by line (lines keep their "\n"; "" once the body ends). */
class Response {
  private buffer = Buffer.alloc(0);
  private ended = false;
  private failure: Error | undefined;
  private waiting: (() => void) | undefined;

  constructor(readonly request: ClientRequest, readonly message: IncomingMessage) {
    message.on("data", (chunk: Buffer) => { this.buffer = Buffer.concat([this.buffer, chunk]); this.wake(); });
    message.on("end", () => { this.ended = true; this.wake(); });
    message.on("error", error => { this.failure = error; this.wake(); });
    message.on("close", () => { this.ended = true; this.wake(); });
  }
  private wake() { const waiting = this.waiting; this.waiting = undefined; waiting?.(); }
  get status() { return this.message.statusCode ?? 0; }
  header(name: string) { const value = this.message.headers[name.toLowerCase()]; return Array.isArray(value) ? value.join(", ") : value; }

  async readline(): Promise<Buffer> {
    for (;;) {
      const newline = this.buffer.indexOf(0x0a);
      if (newline >= 0) { const line = this.buffer.subarray(0, newline + 1); this.buffer = this.buffer.subarray(newline + 1); return line; }
      if (this.failure) throw this.failure;
      if (this.ended) { const rest = this.buffer; this.buffer = Buffer.alloc(0); return rest; }
      await new Promise<void>(done => { this.waiting = done; });
    }
  }
  async read(): Promise<Buffer> {
    const parts: Buffer[] = [];
    for (let line = await this.readline(); line.length > 0; line = await this.readline()) parts.push(line);
    return Buffer.concat(parts);
  }
  close() { this.message.destroy(); this.request.destroy(); }
}

async function verify(framework: string, httpPort: number, httpsPort: number, output: string) {
  const name = "neokit-alpha-" + randomUUID().replaceAll("-", "").slice(0, 10);
  const compose = ["docker", "compose", "-p", name, "-f", join(framework, "deploy/meridian.compose.yaml"),
                   "-f", join(framework, "deploy/meridian.local.compose.yaml")];
  const env = { ...process.env, DOMAIN: "localhost", NEOKIT_BIND_IP: "127.0.0.1", NEOKIT_HTTP_PORT: String(httpPort),
                NEOKIT_HTTPS_PORT: String(httpsPort), NEOKIT_APP_IMAGE: name + ":verified" };
  const run = (command: string[], options: SpawnSyncOptions = { stdio: "inherit" }) => {
    const result = spawnSync(command[0], command.slice(1), { env, cwd: framework, encoding: "utf8", ...options });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`Command ${JSON.stringify(command)} returned non-zero exit status ${result.status ?? result.signal}.`);
    return String(result.stdout ?? "");
  };
  const call = (...args: string[]) => run([...compose, ...args]);
  const capture = (command: string[]) => run(command, { stdio: ["inherit", "pipe", "inherit"] });

  try {
    call("up", "--build", "--wait", "--wait-timeout", "60");
    const temporary = mkdtempSync(join(tmpdir(), "neokit-https-"));
    try {
      const certificate = join(temporary, "root.crt");
      call("cp", "proxy:/data/caddy/pki/authorities/local/root.crt", certificate);
      const ca = readFileSync(certificate);

      /** One request on its own TLS connection to localhost, trusting only the local CA (40 s timeout). */
      const connect = (method: string, path: string, headers: Record<string, string> = {}) =>
        new Promise<Response>((done, fail) => {
          const sent = httpsRequest({ host: "localhost", port: httpsPort, method, path, headers, ca, servername: "localhost", agent: false, timeout: 40000 });
          sent.once("response", message => done(new Response(sent, message)));
          sent.once("timeout", () => sent.destroy(Object.assign(new Error("timed out"), { code: "ETIMEDOUT" })));
          sent.once("error", fail);
          sent.end();
        });
      const request = async (method: string, path: string) => {
        const response = await connect(method, path);
        try { return { status: response.status, body: await response.read() }; } finally { response.close(); }
      };
      // A VM-backed Docker port forward can lag container health.
      const waitForHealth = async () => {
        const deadline = performance.now() + 10000;
        for (;;) {
          try {
            const health = await request("GET", "/healthz");
            check(health.status === 200 && health.body.toString() === '{"ok":true}', { status: health.status, body: health.body.toString() });
            return;
          } catch (error) {
            if ((error as { code?: string }).code !== "ECONNREFUSED" || performance.now() >= deadline) throw error;
            await sleep(200);
          }
        }
      };
      /** The next event with an id: [id, parsed data]. */
      const event = async (response: Response): Promise<[string, unknown]> => {
        let fields = new Map<string, string>();
        for (let line = await response.readline(); line.length > 0; line = await response.readline()) {
          const text = line.toString("utf8");
          if (text.trim() && text.includes(": ")) {
            const content = text.replace(/\r?\n$/, "");
            const at = content.indexOf(": ");
            fields.set(content.slice(0, at), content.slice(at + 2));
          } else if (!text.trim()) {
            if (fields.has("id")) return [fields.get("id")!, JSON.parse(fields.get("data")!)];
            fields = new Map();
          }
        }
        throw new Error("Event stream ended early");
      };

      await waitForHealth();
      let page = await request("GET", "/");
      check(page.status === 200 && page.body.includes("Meridian") && page.body.includes("data-live"), page.status);
      page = await request("GET", "/customers?records=10000");
      check(page.status === 200 && page.body.toString("latin1").split("data-customer-id=").length - 1 === 25, page.status);
      check((await request("POST", "/")).status === 405, "POST / is not refused");
      let stream = await connect("GET", "/api/events");
      check(stream.status === 200 && (stream.header("Content-Type") ?? "").includes("text/event-stream"), stream.header("Content-Type"));
      const [first] = await event(stream);
      const [second, data] = await event(stream);
      stream.close();
      stream = await connect("GET", "/api/events", { "Last-Event-ID": first });
      const replayed = await event(stream);
      check(replayed[0] === second && JSON.stringify(replayed[1]) === JSON.stringify(data), { replayed, second, data });
      const app = capture([...compose, "ps", "-q", "app"]).trim();
      const state = JSON.parse(capture(["docker", "inspect", app]))[0];
      const bindings = state.HostConfig.PortBindings;
      check(!bindings || Object.keys(bindings).length === 0, bindings);
      check(state.HostConfig.ReadonlyRootfs && state.Config.User === "65532:65532", { readonly: state.HostConfig.ReadonlyRootfs, user: state.Config.User });
      const started = performance.now();
      call("stop", "app");
      const shutdownSeconds = (performance.now() - started) / 1000;
      const exitCode = capture(["docker", "inspect", "--format", "{{.State.ExitCode}}", app]).trim();
      check(exitCode === "0" && shutdownSeconds < 37, [exitCode, shutdownSeconds]);
      while ((await stream.readline()).length > 0);
      stream.close();
      call("up", "-d", "--no-build", "--wait", "--wait-timeout", "60");
      await waitForHealth();
      writeFileSync(output, JSON.stringify({
        tls: "verified local CA and localhost hostname", meridian_ssr: true, directory_rows: 25, proxy_mutations: 405,
        sse_replay: true, nonroot_readonly_container: true, shutdown_exit_code: Number(exitCode), shutdown_seconds: shutdownSeconds,
        restart_without_rebuild: true,
      }, null, 2) + "\n");
      console.log("PASS HTTPS, read-only Meridian, SSE replay, graceful stop and restart");
    } finally {
      rmSync(temporary, { recursive: true, force: true });
    }
  } finally {
    call("down", "--volumes");
  }
}

if (import.meta.main) {
  const usage = "usage: alpha_https.ts [-h] --framework FRAMEWORK [--http-port HTTP_PORT] [--https-port HTTPS_PORT] --out OUT";
  const integer = (option: string, text: string | undefined, fallback: number) => {
    if (text === undefined) return fallback;
    if (!/^\s*[+-]?\d+(_\d+)*\s*$/.test(text)) throw new Error(`argument --${option}: invalid int value: '${text}'`);
    return Number(text.trim().replaceAll("_", ""));
  };
  let framework: string, out: string, httpPort: number, httpsPort: number;
  try {
    const { values } = parseArgs({ options: { framework: { type: "string" }, "http-port": { type: "string" }, "https-port": { type: "string" }, out: { type: "string" }, help: { type: "boolean", short: "h" } } });
    if (values.help) { console.log(`${usage}\n\nVerify the Meridian Compose deployment with a trusted local test CA.`); process.exit(0); }
    const missing = ["framework", "out"].filter(option => values[option as "framework" | "out"] === undefined);
    if (missing.length) throw new Error(`the following arguments are required: ${missing.map(option => "--" + option).join(", ")}`);
    httpPort = integer("http-port", values["http-port"], 8189);
    httpsPort = integer("https-port", values["https-port"], 8449);
    framework = resolve(values.framework!);
    out = resolve(values.out!);
  } catch (error) {
    console.error(`${usage}\nalpha_https.ts: error: ${(error as Error).message}`);
    process.exit(2);
  }
  await verify(framework, httpPort, httpsPort, out);
}
