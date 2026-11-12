// HTTP integration for routing, typed templates, mutations, uploads and streaming (examples/features).
import { test, expect } from "bun:test";
import { connect } from "node:net";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Connection, ROOT, Supervised, check, firstBytes, gunzip, neokit, request, running, sleep, withTemporary } from "../lib";

const OUT = join(ROOT, "build/features");
const TOKEN = "test-revalidation-secret-at-least-32-bytes";
neokit(["build", "--app", join(ROOT, "examples/features"), "--out", OUT]);

/** Send `data`, then collect everything until the server closes; `early` is what had
 *  arrived when `marker` first appeared, and `afterMarker` the milliseconds from then to the close. */
function streamed(port: number, data: string, marker: string): Promise<{ wire: Buffer; early: Buffer | undefined; afterMarker: number }> {
  return new Promise((done, fail) => {
    let wire = Buffer.alloc(0), early: Buffer | undefined, seen = 0;
    const socket = connect(port, "127.0.0.1", () => socket.write(data));
    socket.setTimeout(5000, () => { socket.destroy(); fail(new Error("timed out after 5000 ms")); });
    socket.on("data", chunk => {
      wire = Buffer.concat([wire, chunk]);
      if (!early && wire.includes(marker)) { early = wire; seen = performance.now(); }
    });
    socket.on("end", () => done({ wire, early, afterMarker: early ? performance.now() - seen : 0 }));
    socket.on("error", fail);
  });
}

test("features", async () => {
  await withTemporary("neokit-feature-cache-", cache => running(join(OUT, "server"), { env: { NEOKIT_CACHE_DIR: cache, NEOKIT_REVALIDATE_TOKEN: TOKEN } }, async ({ port }) => {
    const home = await request(port, "/");
    expect(home.status).toBe(200);
    for (const text of ["&lt;first&gt;", "<article><h2>Features</h2>", "<li>0</li><li>1</li><li>2</li>", 'href="/org/acme/items/42"']) expect(home.text).toContain(text);
    const item = await request(port, "/org/acme/items/42");
    expect(item.status).toBe(200);
    for (const text of ["acme / 42", "data-web-layout=", "Organization navigation"]) expect(item.text).toContain(text);
    expect((await request(port, "/docs")).text).toContain("Docs: </h1>");
    expect((await request(port, "/docs/a/b")).text).toContain("Docs: a/b");
    for (const path of ["/org/acme/items", "/docs/%2e%2e", "/docs/%2f", "/missing"]) {
      const reply = await request(port, path);
      expect([path, reply.status, reply.text.includes("Feature page missing")]).toEqual([path, 404, true]);
    }
    for (const method of ["POST", "PATCH", "DELETE"]) {
      const reply = await request(port, "/api/42", { method, body: "name=caf%C3%A9", headers: { "Content-Type": "application/x-www-form-urlencoded" } });
      expect([method, reply.status, reply.last("X-NeoKit")]).toEqual([method, 201, "features"]);
      expect(reply.json()).toEqual({ method, id: "42", name: "café" });
    }
    const wrong = await request(port, "/api/42");
    expect([wrong.status, wrong.last("Allow")]).toEqual([405, "POST, PATCH, DELETE, OPTIONS"]);
    expect((await request(port, "/", { method: "POST" })).status).toBe(405);

    // Binary payload above the old 64 KiB input ceiling, including NUL/UTF-8.
    const payload = Buffer.concat(Array.from({ length: 4096 }, () => Buffer.from(Array.from({ length: 256 }, (_, i) => i))));
    const boundary = "NeoKitTestBoundary";
    const multipart = { "Content-Type": `multipart/form-data; boundary=${boundary}` };
    const body = Buffer.concat([
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.bin"\r\nContent-Type: application/octet-stream\r\n\r\n`),
      payload, Buffer.from(`\r\n--${boundary}--\r\n`),
    ]);
    const uploaded = await request(port, "/api/upload", { method: "POST", body, headers: multipart });
    expect([uploaded.status, uploaded.json()]).toEqual([201, { name: "test.bin", bytes: payload.length }]);
    expect((await request(port, "/api/upload", { method: "POST", body: body.subarray(0, body.length - 10), headers: multipart })).status).toBe(400);

    // A chunked upload, as http.client sends it, then the same connection again:
    // framing must consume exactly one request.
    const connection = await Connection.open(port);
    try {
      const chunks: Buffer[] = [];
      for (let i = 0; i < body.length; i += 4096) {
        const part = body.subarray(i, i + 4096);
        chunks.push(Buffer.from(part.length.toString(16).toUpperCase() + "\r\n"), part, Buffer.from("\r\n"));
      }
      await connection.write(Buffer.concat([
        Buffer.from(`POST /api/upload HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nAccept-Encoding: identity\r\nContent-Type: ${multipart["Content-Type"]}\r\nTransfer-Encoding: chunked\r\n\r\n`),
        ...chunks, Buffer.from("0\r\n\r\n"),
      ]));
      const chunked = await connection.reply();
      expect([chunked.status, chunked.json()]).toEqual([201, { name: "test.bin", bytes: payload.length }]);
      await connection.write(`GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nAccept-Encoding: identity\r\n\r\n`);
      const health = await connection.reply();
      expect([health.status, health.json()]).toEqual([200, { ok: true }]);
    } finally { connection.close(); }

    for (const framing of ["Content-Length: 0\r\nTransfer-Encoding: chunked", "Transfer-Encoding: gzip", "Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked", "Content-Length: 1\r\nContent-Length: 1"]) {
      const wire = await firstBytes(port, Buffer.from("POST /api/42 HTTP/1.1\r\nHost: localhost\r\n" + framing + "\r\nConnection: close\r\n\r\n0\r\n\r\n"), received => received.length > 0, 5000);
      expect([framing, wire.length === 0 || wire.toString("latin1").startsWith("HTTP/1.1 400")]).toEqual([framing, true]);
    }

    // An earlier buffered reply must precede the streaming reply.
    const stream = await streamed(port, "GET /api/a HTTP/1.1\r\nHost: localhost\r\n\r\nGET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n", "Shell ready");
    check(stream.early, "the connection closed before the stream shell");
    expect(stream.early.includes("Slow data ready")).toBe(false);
    expect(stream.afterMarker).toBeGreaterThan(200);
    const wire = stream.wire;
    expect(wire.indexOf("405 Method Not Allowed")).toBeGreaterThanOrEqual(0);
    expect(wire.indexOf("405 Method Not Allowed")).toBeLessThan(wire.indexOf("200 OK"));
    expect(wire.includes("Transfer-Encoding: chunked")).toBe(true);
    expect(wire.subarray(wire.length - 5).toString("latin1")).toBe("0\r\n\r\n");
    expect(wire.includes("Slow data ready")).toBe(true);

    const typed = await request(port, "/actions/typed", { method: "POST", body: '{"title":"Typed café","count":42,"enabled":true}', headers: { "Content-Type": "application/json" } });
    expect([typed.status, typed.json()]).toEqual([200, { title: "Typed café", count: 42, enabled: true }]);
    for (const value of ['{"title":"x","count":101,"enabled":true}', '{"title":"x","count":"42","enabled":true}', "[]", "{bad"])
      expect([value, [400, 422].includes((await request(port, "/actions/typed", { method: "POST", body: value, headers: { "Content-Type": "application/json" } })).status)]).toEqual([value, true]);

    const first = await request(port, "/cached");
    expect(first.last("X-NeoKit-Cache")).toBe("MISS");
    await sleep(20);
    const second = await request(port, "/cached");
    expect(second.last("X-NeoKit-Cache")).toBe("HIT");
    expect(second.body.equals(first.body)).toBe(true);
    expect([second.last("Cache-Control"), second.last("Vary")]).toEqual(["public, max-age=1, stale-while-revalidate=10", "X-Web-Fragment"]);
    expect((await request(port, "/cached?anything=1")).last("X-NeoKit-Cache")).toBeUndefined();
    const personal = await request(port, "/cached", { headers: { Cookie: "session=anything" } });
    expect([personal.has("X-NeoKit-Cache"), personal.last("Cache-Control")]).toEqual([false, "no-store"]);
    expect((await request(port, "/invalidate", { method: "POST" })).status).toBe(200);
    const refreshed = await request(port, "/cached");
    expect(refreshed.last("X-NeoKit-Cache")).toBe("MISS");
    await sleep(1050);
    const stale = await request(port, "/cached");
    expect(stale.last("X-NeoKit-Cache")).toBe("STALE");
    expect(stale.body.equals(refreshed.body)).toBe(true);

    const worker = new Supervised(join(OUT, "revalidate"), [], {
      env: { ...process.env, NEO_THREADS: "1", NEOKIT_CACHE_DIR: cache, NEOKIT_REVALIDATE_TOKEN: TOKEN, NEOKIT_REVALIDATE_PORT: String(port) },
    });
    try {
      const until = performance.now() + 10000;
      let revalidated = false;
      while (performance.now() < until) {
        check(worker.running, "revalidate exited:\n" + worker.output);
        const updated = await request(port, "/cached");
        if (!updated.body.equals(refreshed.body) && updated.last("X-NeoKit-Cache") === "HIT") { revalidated = true; break; }
        await sleep(50);
      }
      check(revalidated, "Native background revalidation did not refresh the page");
    } finally {
      // SIGTERM; still running after 10 s fails the test.
      await worker.stop({ grace: 10000, strict: true });
    }

    expect(home.text).toContain('<meta name="description" content="Typed routes &amp; native pages">');
    expect((await request(port, "/sitemap.xml")).text).toContain("https://features.test/docs/a/b");
    expect((await request(port, "/robots.txt")).text).toContain("Sitemap: https://features.test/sitemap.xml");
    const manifest = JSON.parse(readFileSync(join(OUT, "manifest.json"), "utf8"));
    const script = manifest.assets["client.js"];
    const normal = await request(port, script);
    const packed = await request(port, script, { headers: { "Accept-Encoding": "gzip" } });
    expect(packed.last("Content-Encoding")).toBe("gzip");
    expect(gunzip(packed.body).equals(normal.body)).toBe(true);
    // Brotli variants exist when the build environment has the brotli module.
    expect(["br", "gzip"]).toContain((await request(port, script, { headers: { "Accept-Encoding": "br, gzip;q=0.5" } })).last("Content-Encoding")!);
    expect((await request(port, script, { headers: { "Accept-Encoding": "br;q=0, gzip" } })).last("Content-Encoding")).toBe("gzip");
    expect((await request(port, script, { headers: { "Accept-Encoding": "gzip;q=0" } })).has("Content-Encoding")).toBe(false);
    expect((await request(port, script, { headers: { "If-None-Match": normal.last("ETag")! } })).status).toBe(304);
    expect((await request(port, "/healthz")).json()).toEqual({ ok: true });
  }));
});
