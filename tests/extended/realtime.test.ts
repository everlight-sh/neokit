// Independent HTTP/SSE peer for the realtime fixture: auth, HEAD and methods,
// idle cost, shared frames, async loader coexistence, delivery latency,
// replay, reset, bounds, heartbeats, slow readers, shutdown, and delivery
// across forked workers.
import { test, expect } from "bun:test";
import { join } from "node:path";
import { EventStream, ROOT, RawSocket, isNetworkError, neokit, pythonJson, quiet, request, running, sleep, stats, type ServerEvent } from "../lib";

const OUT = join(ROOT, "build/realtime/fixture");
neokit(["build", "--app", join(ROOT, "tests/realtime_fixture"), "--out", OUT]);
const binary = join(OUT, "server");
const ALLOWED = { Cookie: "session=allowed" };
// http.client with a 5 s socket timeout.
const call = (port: number, path: string, options: { method?: string; body?: string | Uint8Array; headers?: Record<string, string> } = {}) =>
  request(port, path, { ...options, timeout: 5000, keepAlive: true });
// Up to 30 s to accept and 15 s to stop after SIGTERM, as the Python suites allowed.
const SERVER = { startup: 30000, stopTimeout: 15000 };

test("single worker: auth, HEAD/methods, idle cost, shared frames, coexistence, latency, replay, reset, bounds, heartbeat, slow readers, shutdown", async () => {
  const result: Record<string, unknown> = {};
  let streams: EventStream[] = [];
  const open = async (...args: Parameters<typeof EventStream.open>) => { const stream = await EventStream.open(...args); streams.push(stream); return stream; };
  const closeAll = () => { for (const stream of streams) stream.close(); streams = []; };
  try {
    await running(binary, SERVER, async ({ port, process: server }) => {
      expect((await call(port, "/feed")).status).toBe(403);
      expect((await call(port, "/feed", { method: "POST", headers: ALLOWED })).status).toBe(405);
      const head = await call(port, "/feed", { method: "HEAD", headers: ALLOWED });
      expect([head.status, head.header("Content-Type")!.startsWith("text/event-stream"), head.body.length]).toEqual([200, true, 0]);
      await quiet(port);
      const snapshot = await call(port, "/feed/snapshot", { headers: ALLOWED });
      expect([snapshot.status, snapshot.json()]).toEqual([200, { snapshot: true }]);

      // An idle subscriber costs no callbacks or channel reads, even when another channel publishes.
      const idle = await open(port);
      expect((await idle.next()).event).toBe("neokit-ready");
      const before = await stats(port);
      await sleep(800);
      const after = await stats(port);
      expect([after.callbacks, after.reads]).toEqual([before.callbacks, before.reads]);
      expect((await call(port, "/publish-other", { method: "POST", body: "{}" })).status).toBe(200);
      await sleep(30);
      const unrelated = await stats(port);
      expect([unrelated.callbacks, unrelated.reads]).toEqual([before.callbacks, before.reads]);
      closeAll();
      await quiet(port);
      result.idle_channel_reads = 0;
      result.idle_subscriber_callbacks = 0;

      const a = await open(port), b = await open(port);
      const publish = async (value: unknown) => {
        const response = await call(port, "/publish", { method: "POST", body: Buffer.from(pythonJson(value, { ensureAscii: false })), headers: { "Content-Type": "application/json" } });
        expect([response.status, response.text]).toEqual([200, response.text]);
        return response.json().sequence as number;
      };
      await publish({ counter: 1, text: "café <script>東京</script>" });
      let first: ServerEvent = await a.message();
      const second = await b.message();
      expect(first).toEqual(second);
      const cached = await stats(port);
      expect([cached.frame_misses, cached.frame_hits, cached.frame_entries]).toEqual([1, 1, 1]);
      result.shared_frame_encoding = "PASS one prepared frame for two subscribers";

      // An async loader keeps waiting while events are delivered.
      let pendingDone = false;
      const pending = call(port, "/wait");
      pending.then(() => { pendingDone = true; }, () => { pendingDone = true; });
      await sleep(30);
      await publish({ counter: 1, coexist: true });
      first = await a.message();
      expect(await b.message()).toEqual(first);
      expect(first.data.coexist).toBeTruthy();
      expect(pendingDone).toBe(false); // Async loader should still be waiting while SSE is delivered
      expect((await pending).status).toBe(200);

      const delays: number[] = [];
      for (let index = 0; index < 24; index++) {
        await sleep([7, 13, 21, 31][index % 4]);
        const began = performance.now();
        await publish({ counter: index, timed: true });
        first = await a.message();
        expect(await b.message()).toEqual(first);
        delays.push(performance.now() - began);
      }
      const ordered = [...delays].sort((x, y) => x - y);
      const p50 = ordered[Math.floor(ordered.length / 2)], p95 = ordered[Math.floor(ordered.length * 0.95)];
      result.arbitrary_publish_delivery_ms = { p50, p95, max: Math.max(...delays), samples: delays };
      expect([p95 < 80, p50 < 30, delays]).toEqual([true, true, delays]);
      closeAll();
      await quiet(port);

      // Replay after a cursor, whatever the query says.
      await publish({ counter: 2 });
      await publish({ counter: 3 });
      const replay = await open(port, "/feed?channel=untrusted", { "Last-Event-ID": first.id! });
      const got = [await replay.message(), await replay.message()];
      expect(got.map(event => event.data.counter)).toEqual([2, 3]);
      closeAll();
      await quiet(port);

      for (let index = 0; index < 16; index++) await publish({ counter: 1000 + index });
      const burst = await open(port, "/feed", { "Last-Event-ID": got.at(-1)!.id! });
      let began = performance.now();
      const counters: number[] = [];
      for (let index = 0; index < 16; index++) counters.push((await burst.message()).data.counter);
      expect(counters).toEqual(Array.from({ length: 16 }, (_, index) => 1000 + index));
      result.replay_16_events_seconds = (performance.now() - began) / 1000;
      expect(result.replay_16_events_seconds).toBeLessThan(0.2);
      closeAll();
      await quiet(port);

      // A cursor older than the history window resets to the latest event.
      for (let counter = 4; counter < 145; counter++) await publish({ counter });
      const gap = await open(port, "/feed", { "Last-Event-ID": first.id! });
      expect((await gap.next()).event).toBe("neokit-ready");
      expect((await gap.next()).event).toBe("neokit-reset");
      expect((await gap.message()).data.counter).toBe(144);
      closeAll();
      await quiet(port);
      // So do malformed and future cursors.
      for (const cursor of ["1.1", first.id!.split(".")[0] + ".999999"]) {
        const stream = await open(port, "/feed", { "Last-Event-ID": cursor });
        expect((await stream.next()).event).toBe("neokit-ready");
        expect((await stream.next()).event).toBe("neokit-reset");
        expect((await stream.message()).data.counter).toBe(144);
        closeAll();
      }
      expect((await call(port, "/feed?after=bad", { headers: ALLOWED })).status).toBe(400);
      expect((await call(port, "/publish", { method: "POST", body: "invalid" })).status).toBe(400);
      expect((await call(port, "/publish", { method: "POST", body: pythonJson({ data: "x".repeat(5000) }) })).status).toBe(400);

      // Heartbeats arrive about every 15 s and do not read channel data.
      const heartbeat = await open(port, "/feed", {}, 18000);
      await heartbeat.message();
      const beforeHeartbeat = await stats(port);
      began = performance.now();
      let event: ServerEvent;
      do event = await heartbeat.next(true); while (!("comment" in event));
      const elapsed = (performance.now() - began) / 1000;
      expect([elapsed >= 14 && elapsed <= 17, elapsed, event]).toEqual([true, elapsed, event]);
      const afterHeartbeat = await stats(port);
      expect(afterHeartbeat.reads).toBe(beforeHeartbeat.reads); // Heartbeat must not poll channel data
      closeAll();
      await quiet(port);
      result.heartbeat_seconds = elapsed;

      // Subscribers that never read do not hold up publishers or other requests.
      const slow = Array.from({ length: 4 }, () => {
        const peer = RawSocket.connect(port);
        peer.sendAll(Buffer.from("GET /feed HTTP/1.1\r\nHost: localhost\r\nCookie: session=allowed\r\n\r\n"));
        return peer;
      });
      try {
        for (let counter = 0; counter < 200; counter++) await publish({ counter, data: "x".repeat(3500) });
        expect((await call(port, "/healthz")).status).toBe(200);
      } finally { for (const peer of slow) peer.close(); }
      await quiet(port);

      // SIGTERM ends open streams and the server within a second.
      await open(port);
      began = performance.now();
      process.kill(-server.pid, "SIGTERM");
      const stopped = await server.wait(2000);
      expect(stopped).toBe(true);
      result.shutdown_seconds = (performance.now() - began) / 1000;
      closeAll();
      expect(result.shutdown_seconds).toBeLessThan(1);
      result.single_worker = "PASS auth/HEAD/methods, static-over-dynamic stream precedence, async loader/SSE coexistence, matching subscribers, replay, reset, UTF-8, heartbeat, bounds, slow readers, cleanup, shutdown";
    });
  } finally {
    closeAll();
    console.log(JSON.stringify({ ...result, arbitrary_publish_delivery_ms: { ...(result.arbitrary_publish_delivery_ms as object), samples: undefined } }, null, 2));
  }
});

test("two workers: matching events across forked workers after connection churn", async () => {
  let streams: EventStream[] = [];
  try {
    // Cross-worker shared-memory publication: NEO_THREADS=2.
    await running(binary, { workers: 2, startup: 5000, stopTimeout: 3000 }, async ({ port }) => {
      const until = performance.now() + 5000;
      for (;;) {
        try {
          if ((await call(port, "/healthz")).status === 200) break;
        } catch (error) {
          if (!isNetworkError(error) || performance.now() > until) throw error;
        }
        if (performance.now() > until) throw new Error("/healthz did not answer 200 within 5 s");
        await sleep(10);
      }
      streams = [await EventStream.open(port), await EventStream.open(port), await EventStream.open(port)];
      streams.shift()!.close();
      for (let index = 0; index < 20; index++) expect((await call(port, "/healthz")).status).toBe(200);
      expect((await call(port, "/publish", { method: "POST", body: '{"cross_worker":true}' })).status).toBe(200);
      const messages = [await streams[0].message(), await streams[1].message()];
      expect(messages[0]).toEqual(messages[1]);
      expect(messages[0].data.cross_worker).toBeTruthy();
    });
  } finally { for (const stream of streams) stream.close(); }
});
