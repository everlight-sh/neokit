// Native SSR, asset embedding, framing, export and independent models, on the studio example.
import { test, expect } from "bun:test";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { ROOT, auditBinary, check, childProcesses, exchange, firstBytes, neokit, parseResponses, request, running, sleep } from "../lib";

const OUT = join(ROOT, "build/studio");
const EXPORT = join(ROOT, "build/test-export");
const decode = (text: string) => text.replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const stamp = (body: string) => Number(/Rendered on request[^<]*?([0-9]{10,})/.exec(body)![1]);
const STRESS_ROW = /<tr data-row-id=/g;
const count = (text: string, pattern: RegExp) => (text.match(pattern) ?? []).length;

neokit(["build", "--out", OUT]);
const binary = join(OUT, "server");
const manifest = JSON.parse(readFileSync(join(OUT, "manifest.json"), "utf8"));

test("native binary has no imports", () => auditBinary(binary));

for (const workers of [1, 2]) test(`native SSR with ${workers} worker(s)`, async () => {
  await running(binary, { workers }, async ({ port, process: server }) => {
    const home = await request(port);
    expect(home.status).toBe(200);
    for (const text of ["Build your next", 'data-web-widget="counter"', "NeoKit", "data-web-form", "LIVE SERVER RENDER", "Rendered on request"]) expect(home.text).toContain(text);
    expect(home.text).not.toContain("Axon Web");
    expect(home.header("Cache-Control")).toBe("no-store");
    expect(home.header("X-Content-Type-Options")).toBe("nosniff");
    expect(Math.abs(Date.parse(home.header("Date")!) - Date.now())).toBeLessThan(120000);
    expect(home.header("Content-Security-Policy")!.startsWith("default-src 'self'")).toBe(true);
    expect((await request(port, "/about")).has("Date") && (await request(port, "/missing")).has("Date")).toBe(true);
    const first = stamp(home.text);
    await sleep(20);
    expect(stamp((await request(port)).text)).toBeGreaterThan(first);

    const head = await request(port, "/", { method: "HEAD" });
    expect(head.status).toBe(200);
    expect(head.body.length).toBe(0);
    expect(Number(head.header("Content-Length"))).toBeGreaterThan(500);
    expect((await request(port, "/", { method: "POST" })).status).toBe(405);
    for (const path of ["/missing", "/_web/../src/lib.neo", "/notes/", "/notes/%2f", "/notes/%2e%2e", "/notes/%ff", "/notes/%00", "/notes/%zz"])
      expect([path, (await request(port, path)).status]).toEqual([path, 404]);
    for (const path of ["/notes/native", "/notes/static", "/notes/islands", "/about", "/about/", "/stress"])
      expect([path, (await request(port, path)).status]).toEqual([path, 200]);
    const about = await request(port, "/about");
    expect(about.text).not.toContain("<script");
    expect(about.body.equals(readFileSync(join(OUT, "snapshots/about/index.html")))).toBe(true);

    const value = '<script>alert("hi")</script> & café';
    const escaped = "&lt;script&gt;alert(&quot;hi&quot;)&lt;/script&gt; &amp; café";
    const fragment = (await request(port, "/greet?name=" + encodeURIComponent(value), { headers: { "X-Web-Fragment": "1" } })).text;
    expect(fragment).toContain(escaped);
    expect(fragment.toLowerCase()).not.toContain("<!doctype");
    expect(fragment).not.toContain("<script>");
    const full = (await request(port, "/greet?name=Marcos")).text;
    expect(full.toLowerCase()).toContain("<!doctype html>");
    expect(full).toContain("Hello, Marcos");
    expect((await request(port, "/?name=" + encodeURIComponent(value))).text).toContain(escaped);
    const dynamic = await request(port, "/notes/" + encodeURIComponent("<img src=x onerror=alert(1)>"));
    expect(dynamic.status).toBe(200);
    expect(dynamic.text).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(dynamic.text).not.toContain("<img src=x");

    for (const [url, info] of Object.entries<any>(manifest.files)) {
      const asset = await request(port, url);
      expect([url, asset.status, asset.body.length, createHash("sha256").update(asset.body).digest("hex")]).toEqual([url, 200, info.bytes, info.sha256]);
      const policy = asset.header("Cache-Control")!;
      if (url === "/_web/neokit-client.js" || !url.startsWith("/_web/")) expect([url, policy]).toEqual([url, "public, no-cache"]);
      else expect(policy).toContain("immutable");
      if (url.endsWith(".js")) expect(asset.header("Content-Type")!.startsWith("text/javascript")).toBe(true);
      // Conditional GET: weak comparison, and 304 keeps the validators and cache policy.
      const etag = asset.header("ETag")!;
      for (const supplied of [etag, etag.slice(2), '"other", ' + etag, "*"]) {
        const revalidated = await request(port, url, { headers: { "If-None-Match": supplied } });
        expect([url, supplied, revalidated.status, revalidated.body.length, revalidated.header("ETag"), revalidated.header("Cache-Control")]).toEqual([url, supplied, 304, 0, etag, policy]);
      }
      expect((await request(port, url, { headers: { "If-None-Match": '"other"' } })).status).toBe(200);
    }
    const scripts = Object.keys(manifest.files).filter(url => url.endsWith(".js"));
    const client = scripts.reduce((largest, url) => (manifest.files[url].bytes > manifest.files[largest].bytes ? url : largest));
    expect((await request(port, client, { headers: { "Accept-Encoding": "gzip;q=0, identity" } })).has("Content-Encoding")).toBe(false);
    const packed = await request(port, client, { headers: { "Accept-Encoding": "gzip" } });
    expect([packed.header("Content-Encoding"), packed.header("Vary"), packed.body[0], packed.body[1]]).toEqual(["gzip", "Accept-Encoding", 0x1f, 0x8b]);
    expect(["gzip", "br"]).toContain((await request(port, client, { headers: { "Accept-Encoding": "*" } })).header("Content-Encoding")!);
    const aboutHeaders = await request(port, "/about");
    expect(aboutHeaders.header("Cache-Control")).toBe("public, no-cache");
    expect(aboutHeaders.header("ETag")!.startsWith('W/"')).toBe(true);

    for (const n of [1, 100, 1000, 10000]) {
      const stress = await request(port, "/stress?n=" + n);
      expect(stress.status).toBe(200);
      expect(count(stress.text, STRESS_ROW)).toBe(Math.min(n, 32));
      const embedded = /<template data-web-data="stress">(.*?)<\/template>/s.exec(stress.text)![1];
      const data = JSON.parse(decode(embedded));
      expect(data.count).toBe(n);
      expect(data.values).toEqual(Array.from({ length: n }, (_, i) => ((i + 1) * 17) % 1000));
      expect(stress.text).toContain('data-web-widget="list-stress"');
      expect(stress.text).toContain("Redraw visible window (comparison)");
      expect(count(stress.text, /data-note aria-label=/g)).toBe(Math.min(n, 32));
    }
    expect(count((await request(port, "/stress?n=-1")).text, STRESS_ROW)).toBe(32);
    expect(count((await request(port, "/stress?n=100&render=full")).text, STRESS_ROW)).toBe(100);
    const api = (await request(port, "/api/status")).json();
    expect([api.framework, api.renderer, api.react, api.stamp > 0]).toEqual(["neokit", "native", false, true]);

    // Keep-alive: twelve requests on one connection.
    const paths = Array.from({ length: 3 }, () => ["/", "/about", "/notes/native", "/api/status"]).flat();
    const kept = await exchange(port, Buffer.from(paths.map((path, i) => `GET ${path} HTTP/1.1\r\nHost: localhost\r\nAccept-Encoding: identity\r\n${i === paths.length - 1 ? "Connection: close\r\n" : ""}\r\n`).join("")));
    expect(parseResponses(kept, paths.map(() => "GET")).map(reply => reply.status)).toEqual(paths.map(() => 200));

    // 48 requests, 12 at a time.
    for (let batch = 0; batch < 48; batch += 12) {
      const replies = await Promise.all(Array.from({ length: 12 }, (_, j) => request(port, "/greet?name=visitor" + (batch + j), { headers: { "X-Web-Fragment": "1" } })));
      replies.forEach((reply, j) => expect([reply.status, reply.text.includes("Hello, visitor" + (batch + j))]).toEqual([200, true]));
    }

    // A pipelined HEAD then GET: the HEAD has no body, the GET ends the connection.
    const pipelined = await exchange(port, Buffer.from("HEAD /about HTTP/1.1\r\nHost: localhost\r\n\r\nGET /api/status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"));
    const [headReply, getReply] = parseResponses(pipelined, ["HEAD", "GET"]);
    expect([headReply.status, getReply.status, getReply.json().react]).toEqual([200, 200, false]);
    // Nothing follows the GET body before the close.
    expect(pipelined.subarray(pipelined.length - getReply.body.length).equals(getReply.body)).toBe(true);

    // Oversized headers get an answer rather than a silent close.
    const big = await firstBytes(port, Buffer.from("GET / HTTP/1.1\r\nHost: localhost\r\nX-Big: " + "a".repeat(20000) + "\r\n\r\n"), received => received.includes("\r\n"));
    expect(big.toString("latin1").startsWith("HTTP/1.1 431")).toBe(true);

    if (workers > 1 && process.platform === "linux") {
      // The supervisor replaces a worker that dies.
      const before = await childProcesses(server.pid!);
      expect(before.length).toBe(workers);
      process.kill(before[0], "SIGKILL");
      let replaced = false;
      for (let attempt = 0; attempt < 100 && !replaced; attempt++) {
        const after = await childProcesses(server.pid!);
        replaced = after.length === workers && !after.includes(before[0]);
        if (!replaced) await sleep(50);
      }
      check(replaced, "worker was not replaced");
      for (let i = 0; i < 8; i++) expect((await request(port)).status).toBe(200);
    }
  });
});

test("static export", async () => {
  neokit(["export", "--out", OUT, "--dest", EXPORT]);
  const home = readFileSync(join(EXPORT, "index.html"), "utf8");
  expect(home).toContain("STATIC EXPORT");
  expect(home).not.toContain("data-web-form");
  expect(home).toContain('data-web-widget="counter"');
  expect(readFileSync(join(EXPORT, "about/index.html"), "utf8")).not.toContain("<script");
  expect(count(readFileSync(join(EXPORT, "stress/index.html"), "utf8"), STRESS_ROW)).toBe(32);
  expect(existsSync(join(EXPORT, "greet/index.html")) || existsSync(join(EXPORT, "api/status/index.html"))).toBe(false);

  // The export works from any static file server.
  const files = Bun.serve({ port: 0, hostname: "127.0.0.1", fetch(incoming) {
    let path = join(EXPORT, decodeURIComponent(new URL(incoming.url).pathname));
    if (existsSync(path) && statSync(path).isDirectory()) path = join(path, "index.html");
    return existsSync(path) ? new Response(Bun.file(path)) : new Response("missing", { status: 404 });
  } });
  try {
    expect((await request(files.port!)).status).toBe(200);
    for (const url of Object.keys(manifest.files)) expect([url, (await request(files.port!, url)).status]).toEqual([url, 200]);
  } finally { files.stop(true); }

  const strict = neokit(["export", "--out", OUT, "--dest", EXPORT, "--strict"], { allowFailure: true });
  expect(strict.status).not.toBe(0);
  expect(strict.stderr).toContain("Server-only routes");
});
