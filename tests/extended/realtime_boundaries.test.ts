// Exact large SSE replay across callback byte budgets, including reset. Each
// event is JSON padded with embedded newlines, so every line becomes its own
// "data:" line on the wire. 3,900 newlines fit the default 4,096-byte event
// limit (tests/realtime_fixture); 7,900 need tests/realtime_limits_fixture
// (max_event_bytes=8192) and encode past the 48 KiB callback budget.
import { test, expect } from "bun:test";
import { join } from "node:path";
import { EventStream, ROOT, neokit, quiet, request, running, type ServerEvent } from "../lib";

const FIXTURE = join(ROOT, "build/realtime/fixture");
const LIMITS = join(ROOT, "build/realtime/limits");
neokit(["build", "--app", join(ROOT, "tests/realtime_fixture"), "--out", FIXTURE]);
neokit(["build", "--app", join(ROOT, "tests/realtime_limits_fixture"), "--out", LIMITS]);

const publish = (port: number, body: string | Uint8Array) => request(port, "/publish", { method: "POST", body, timeout: 5000, keepAlive: true });

async function boundaries(binary: string, newlines: number) {
  // Up to 30 s to accept and 15 s to stop after SIGTERM.
  await running(binary, { startup: 30000, stopTimeout: 15000 }, async ({ port }) => {
    const anchor = await EventStream.open(port);
    let initial: ServerEvent | undefined;
    try {
      expect((await anchor.next()).event).toBe("neokit-ready");
      expect((await publish(port, '{"counter":0}')).status).toBe(200);
      initial = await anchor.message();
    } finally { anchor.close(); }
    await quiet(port);
    for (let counter = 1; counter < 9; counter++) {
      const wire = Buffer.concat([Buffer.from("{"), Buffer.alloc(newlines, "\n"), Buffer.from(`"counter":${counter},"text":"東京 café"}`)]);
      expect((await publish(port, wire)).status).toBe(200);
    }
    const replay = await EventStream.open(port, "/feed", { "Last-Event-ID": initial!.id! });
    try {
      const events = [];
      for (let index = 0; index < 8; index++) events.push(await replay.message());
      expect(events.map(event => event.data)).toEqual(Array.from({ length: 8 }, (_, index) => ({ counter: index + 1, text: "東京 café" })));
      expect(new Set(events.map(event => event.id)).size).toBe(8);
    } finally { replay.close(); }
    await quiet(port);
    const future = initial!.id!.split(".")[0] + ".999999";
    const reset = await EventStream.open(port, "/feed", { "Last-Event-ID": future });
    try {
      expect((await reset.next()).event).toBe("neokit-ready");
      expect((await reset.next()).event).toBe("neokit-reset");
      expect((await reset.message()).data).toEqual({ counter: 8, text: "東京 café" });
    } finally { reset.close(); }
    const state = await quiet(port);
    expect(state.frame_entries).toBeLessThanOrEqual(64);
  });
}

test("3900 newlines/event: eight exact replayed UTF-8 events, reset/latest, cleanup and bounded cache", () => boundaries(join(FIXTURE, "server"), 3900));
test("7900 newlines/event (8 KiB events, past the 48 KiB callback budget): the same", () => boundaries(join(LIMITS, "server"), 7900));
