// Real PostgreSQL + delayed HTTP: async pages/APIs, streaming and cancellation.
//
// PostgreSQL is an independent local test service (SCRAM; user "test", password
// "pencil", database "test"); this test owns only its HTTP peers and NeoKit
// processes. Set NEOKIT_ASYNC_PG_PORT to the disposable test database's port,
// or run the suite inside a private cluster (`./neokit test --postgres` does this):
//
//   bun tests/tools/postgres.ts -- bun test --timeout 600000 ./tests/postgres
//
// Optional: NEOKIT_ASYNC_BINARY (by default examples/features is built into
// build/async-features), NEOKIT_ASYNC_WORKERS (default 1) and NEOKIT_ASYNC_OUT
// (a file for the timings and counters as JSON).
import { test, expect } from "bun:test";
import { connect, type Socket } from "node:net";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { DelayedPeer, ROOT, Supervised, check, freePort, neokit, pythonJson, request as send, sleep } from "../lib";

const PG_PORT = process.env.NEOKIT_ASYNC_PG_PORT;
const WORKERS = Number(process.env.NEOKIT_ASYNC_WORKERS ?? "1");
const OUT = process.env.NEOKIT_ASYNC_OUT;
const binary = process.env.NEOKIT_ASYNC_BINARY ? resolve(process.env.NEOKIT_ASYNC_BINARY) : join(ROOT, "build/async-features/server");
if (PG_PORT && !process.env.NEOKIT_ASYNC_BINARY) neokit(["build", "--app", join(ROOT, "examples/features"), "--out", join(ROOT, "build/async-features")]);

const seconds = (since: number) => (performance.now() - since) / 1000;
const MARKERS: [string, string][] = [["shell", "Shell ready"], ["network", "Network &lt;fast&gt;"], ["database", "Database &lt;row&gt;"],
  ["slow", "Network &lt;slow&gt;"], ["error", 'data-web-state="error"']];

/** A raw GET without Connection: close, left open for the caller (12 s read timeout). */
function raw(port: number, path = "/async"): Socket {
  const socket = connect(port, "127.0.0.1", () => socket.write(`GET ${path} HTTP/1.1\r\nHost: localhost\r\n\r\n`));
  socket.setTimeout(12000, () => socket.destroy(new Error(`GET ${path}: timed out after 12000 ms`)));
  return socket;
}

/** Read until `marker` arrives, then stop reading and leave the socket open. */
function readUntil(socket: Socket, marker: string): Promise<void> {
  return new Promise((done, fail) => {
    let wire = Buffer.alloc(0);
    const data = (chunk: Buffer) => {
      wire = Buffer.concat([wire, chunk]);
      if (!wire.includes(marker)) return;
      socket.off("data", data);
      socket.pause();
      done();
    };
    socket.on("data", data);
    socket.once("end", () => fail(new Error(`closed before ${marker}: ${wire.toString("latin1").slice(-300)}`)));
    socket.once("error", fail);
  });
}

test("async rendering with PostgreSQL and delayed HTTP peers", async () => {
  check(PG_PORT && Number.isInteger(Number(PG_PORT)), "Set NEOKIT_ASYNC_PG_PORT to a disposable PostgreSQL (SCRAM user test/pencil, database test), " +
    "or run: bun tests/tools/postgres.ts -- bun test --timeout 600000 ./tests/postgres");
  const peer = await DelayedPeer.start();
  const port = await freePort();
  const server = new Supervised(binary, [String(port)], {
    env: { ...process.env, NEO_THREADS: String(WORKERS), NEOKIT_ASYNC_HTTP_PORT: String(peer.port), NEOKIT_ASYNC_PG_PORT: PG_PORT! },
  });
  // http.client with a 12 s socket timeout (a bodiless POST sends Content-Length: 0).
  const request = (path: string, method = "GET") => send(port, path, { method, timeout: 12000, keepAlive: true });
  const quiet = async () => {
    const deadline = performance.now() + 3000;
    let stats: any;
    while (performance.now() < deadline) {
      stats = (await request("/async-stats")).json();
      if (stats.live === stats.captures && stats.captures === stats.results && stats.results === 0) return stats;
      await sleep(10);
    }
    throw new Error("check failed: " + JSON.stringify(stats));
  };
  const results: Record<string, unknown> = {};
  const sockets: Socket[] = [];
  let failed = false;
  try {
    const first = await server.firstLine();
    check(first.startsWith("neo: serving"), first);
    const initial = await quiet();

    // Streaming: the shell first, then each boundary as its own loader finishes.
    const start = performance.now();
    const seen: Record<string, number> = {};
    const data = await new Promise<Buffer>((done, fail) => {
      let data = Buffer.alloc(0);
      const socket = raw(port);
      socket.on("data", block => {
        data = Buffer.concat([data, block]);
        const now = seconds(start);
        for (const [name, marker] of MARKERS) if (data.includes(marker) && !(name in seen)) seen[name] = now;
      });
      socket.once("end", () => { socket.destroy(); done(data); });
      socket.once("error", fail);
    });
    check(data.subarray(-5).equals(Buffer.from("0\r\n\r\n")), data.subarray(-300).toString("latin1"));
    check(seen.shell < 0.3 && seen.network < seen.database && seen.database < seen.slow && seen.error < seen.slow, seen);
    const headEnd = data.indexOf("\r\n\r\n");
    check(data.includes("data-web-stream-end=") && !data.includes("Database <row>") && headEnd >= 0 && !data.subarray(0, headEnd).includes("Content-Length:"),
      data.subarray(-400).toString("latin1"));
    // Streamed pages carry a Date like buffered responses (Neo 0.2.6 http_render_start_with_tail_at).
    const streamHead = data.subarray(0, headEnd).toString("latin1");
    check(/\r\nDate: [A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT(\r\n|$)/.test(streamHead), streamHead);
    results.stream_seconds = seen;
    console.log("PASS independent DB/network completion, shell-first framing and isolated timeout", seen);
    await quiet();

    // Async pages and APIs wait on the network without blocking fast requests.
    const pending = [...Array.from({ length: 8 }, () => request("/async-api")), request("/async-page")]
      .map(reply => reply.then(value => ({ value }), error => ({ error })));
    await sleep(100);
    const begin = performance.now();
    for (let i = 0; i < 30; i++) expect((await request("/healthz")).status).toBe(200);
    const fast = seconds(begin);
    expect(fast).toBeLessThan(0.3);
    for (const [i, outcome] of (await Promise.all(pending)).entries()) {
      if ("error" in outcome) throw outcome.error;
      const reply = outcome.value;
      check(reply.status === 200, [reply.status, reply.text]);
      check(reply.text.includes(i === 8 ? "Network &lt;slow&gt;" : "Network <slow>"), reply.text.slice(0, 200));
      expect(reply.header("Connection")).toBe("close");
    }
    results.fast_during_async_seconds = fast;
    console.log("PASS async pages/APIs yield during network waits; fast requests", fast);
    await quiet();

    let began = performance.now();
    const expired = await request("/async-api?target=/timeout");
    check(expired.status === 504 && expired.body.equals(Buffer.from("Async render expired")), [expired.status, expired.text]);
    expect(seconds(began)).toBeLessThan(3);
    await quiet();

    // No shell or upstream body can incidentally discover this disconnect.
    // The HTTP worker must observe EOF while all loaders are still waiting.
    let before = peer.cancelled;
    for (let i = 0; i < 12; i++) { const socket = raw(port, "/async-api?target=/hold"); socket.on("error", () => {}); sockets.push(socket); }
    const until = performance.now() + 1000;
    while (performance.now() < until && (await request("/async-stats")).json().live < 12) await sleep(10);
    for (const socket of sockets) socket.destroy();
    sockets.length = 0;
    began = performance.now();
    await quiet();
    expect(seconds(began)).toBeLessThan(0.4);
    check(peer.cancelled > before, [before, peer.cancelled]);

    before = peer.cancelled;
    for (let i = 0; i < 12; i++) {
      const socket = raw(port);
      sockets.push(socket);
      await readUntil(socket, "Shell ready");
      socket.on("error", () => {});
    }
    await sleep(60);
    for (const socket of sockets) socket.destroy();
    sockets.length = 0;
    await quiet();
    check(peer.cancelled > before, [before, peer.cancelled]);
    console.log("PASS disconnect cancellation releases tasks, captures, results and upstream sockets");

    expect((await request("/async", "HEAD")).status).toBe(200);
    expect((await request("/async", "POST")).status).toBe(405);
    const head = await request("/async-api", "HEAD");
    check(head.status === 200 && head.body.length === 0, [head.status, head.text]);
    await quiet();
    results.upstream_cancelled = peer.cancelled;
    results.final = await quiet();
    expect(results.final).toEqual(initial);
    if (OUT) {
      mkdirSync(dirname(resolve(OUT)), { recursive: true });
      writeFileSync(resolve(OUT), pythonJson(results, { indent: 2 }) + "\n");
    }
    console.log("PASS HEAD/method dispatch, buffered deadlines and clean idle resource counts");
  } catch (error) {
    failed = true;
    throw error;
  } finally {
    for (const socket of sockets) socket.destroy();
    try {
      await server.stop({ grace: 4000, strict: true });
    } finally {
      if (failed && server.output) console.error("Native server diagnostics:\n" + server.output);
      await peer.close();
    }
  }
}, 120000);
