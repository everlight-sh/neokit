// Real login, CSRF, per-user authorization and durable concurrent CRUD (examples/dashboard).
// NEOKIT_DASHBOARD_BINARY runs it against a prebuilt server (or a wrapper that
// starts one, as tests/tools/linux_container.ts does) instead of building it.
import { test, expect } from "bun:test";
import { createHmac, pbkdf2Sync } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, freePort, neokit, pythonJson, request, running, urlencode, withTemporary } from "../lib";

const OUT = join(ROOT, "build/dashboard");
const binary = process.env.NEOKIT_DASHBOARD_BINARY ?? (neokit(["build", "--app", join(ROOT, "examples/dashboard"), "--out", OUT]), join(OUT, "server"));

const b64 = (value: string | Uint8Array) => Buffer.from(value).toString("base64url");
/** A v1 token (no issue time, still accepted) or, with `issued`, a v2 token. */
function signed(subject: string, key: string, issued?: number): [string, string] {
  // Use exactly 32 bytes, independently of the implementation's CSPRNG.
  const csrf = b64("0123456789abcdef0123456789abcdef");
  const expiry = Date.now() + 3600000;
  const body = issued !== undefined ? `v2.${b64(subject)}.${csrf}.${issued}.${expiry}` : `v1.${b64(subject)}.${csrf}.${expiry}`;
  return [body + "." + b64(createHmac("sha256", key).update(body).digest()), csrf];
}
function csrfOf(page: string) {
  const match = /name="_csrf" value="([^"]+)"/.exec(page);
  check(match, "no _csrf field");
  return match[1];
}
const FORM = "application/x-www-form-urlencoded";

// Headers are read last-value-wins (.last()), as Python's dict(response.getheaders()) keeps them.
test("dashboard", async () => {
  await withTemporary("neokit-dashboard-", async directory => {
    const key = "test-session-key-32-characters-long-only-for-tests";
    const salt = Buffer.from("0123456789abcdef"), password = "correct-horse-test-password";
    const encoded = "p1.600000." + b64(salt) + "." + b64(pbkdf2Sync(password, salt, 600000, 32, "sha256"));
    const chosen = await freePort(), origin = `http://127.0.0.1:${chosen}`;
    const environment = { NEOKIT_SESSION_SECRET: key, NEOKIT_ORIGIN: origin, NEOKIT_ADMIN_PASSWORD_HASH: encoded, NEOKIT_DATA_DIR: directory };
    const stored = () => JSON.parse(readFileSync(join(directory, "tasks.json"), "utf8"));
    let cookie = "", wrong = "", form: Record<string, string> = {}, ids: string[] = [];

    await running(binary, { workers: 2, env: environment, port: chosen }, async ({ port }) => {
      expect((await request(port, "/dashboard")).status).toBe(303);
      let reply = await request(port, "/login");
      expect(reply.status).toBe(200);
      cookie = reply.last("Set-Cookie")!.split(";")[0];
      expect(reply.last("Set-Cookie")).toContain("HttpOnly");
      expect(reply.last("Set-Cookie")).toContain("SameSite=Lax");
      const login = urlencode({ _csrf: csrfOf(reply.text), username: "admin", password });
      expect((await request(port, "/login", { method: "POST", body: login, headers: { Cookie: cookie, Origin: "https://evil.test", "Content-Type": FORM } })).status).toBe(403);
      const start = performance.now();
      reply = await request(port, "/login", { method: "POST", body: login, headers: { Cookie: cookie, Origin: origin, "Content-Type": FORM } });
      expect([reply.status, reply.status === 303 ? "" : reply.text]).toEqual([303, ""]);
      console.log(`PASS 600000-round native password login (${((performance.now() - start) / 1000).toFixed(3)}s)`);
      cookie = reply.last("Set-Cookie")!.split(";")[0];
      const csrf = csrfOf((await request(port, "/dashboard", { headers: { Cookie: cookie } })).text);
      const headers = { Cookie: cookie, Origin: origin, "X-CSRF-Token": csrf, "Content-Type": "application/json" };
      const post = (path: string, body: string, fields: Record<string, string>, method = "POST") => request(port, path, { method, body, headers: fields });
      expect((await post("/api/tasks", '{"title":"blocked"}', { ...headers, Origin: "https://evil.test" })).status).toBe(403);
      expect((await post("/api/tasks", '{"title":"blocked"}', { ...headers, "X-CSRF-Token": "bad" })).status).toBe(403);
      const created = await post("/api/tasks", pythonJson({ title: "<script> café & test" }), headers);
      expect([created.status, created.status === 201 ? "" : created.text]).toEqual([201, ""]);
      const task: string = created.json().id;
      const dashboard = (await request(port, "/dashboard", { headers: { Cookie: cookie } })).text;
      expect(dashboard).toContain("&lt;script&gt; caf");
      expect(dashboard).not.toContain("<script> caf");

      const [other, otherCsrf] = signed("other-user", key);
      const otherHeaders = { Cookie: "neokit_session=" + other, Origin: origin, "X-CSRF-Token": otherCsrf, "Content-Type": "application/json" };
      expect((await post("/api/tasks/" + task, '{"title":"stolen"}', otherHeaders, "PATCH")).status).toBe(404);
      expect((await request(port, "/dashboard", { headers: { Cookie: "neokit_session=" + other } })).text).not.toContain(task);
      expect((await post("/api/tasks/" + task, '{"title":"Updated"}', headers, "PATCH")).status).toBe(200);
      const updated = stored().tasks.find((value: any) => value.id === task);
      check(updated, "updated task missing from tasks.json");
      expect(updated.title).toBe("Updated");

      // 24 creations, 8 at a time.
      ids = new Array(24);
      let next = 0;
      await Promise.all(Array.from({ length: 8 }, async () => {
        while (next < 24) {
          const i = next++;
          const reply = await post("/api/tasks", pythonJson({ title: `Concurrent ${i}` }), headers);
          expect([i, reply.status, reply.status === 201 ? "" : reply.text]).toEqual([i, 201, ""]);
          ids[i] = reply.json().id;
        }
      }));
      expect(new Set(ids).size).toBe(24);
      const tasks = stored();
      expect([tasks.schema, tasks.tasks.length]).toEqual([1, 25]);
      expect((await post("/api/tasks/" + task, "{}", headers, "DELETE")).status).toBe(200);
      expect((await post("/api/tasks/" + task, "{}", headers, "DELETE")).status).toBe(404);
      expect((await request(port, "/dashboard", { headers: { Cookie: cookie + "broken" } })).status).toBe(303);
      const logout = await post("/logout", urlencode({ _csrf: csrf }), { Cookie: cookie, Origin: origin, "Content-Type": FORM });
      expect(logout.status).toBe(303);
      expect(logout.last("Set-Cookie")).toContain("Max-Age=0");
      // Logout revokes the session itself, so a copy of the cookie stops working.
      expect((await request(port, "/dashboard", { headers: { Cookie: cookie } })).status).toBe(303);
      expect((await post("/api/tasks/" + ids[0], '{"title":"after logout"}', headers, "PATCH")).status).toBe(401);

      // One successful login is already counted; ten attempts a minute per client.
      reply = await request(port, "/login");
      expect(reply.status).toBe(200);
      const anonymous = reply.last("Set-Cookie")!.split(";")[0];
      form = { Cookie: anonymous, Origin: origin, "Content-Type": FORM };
      wrong = urlencode({ _csrf: csrfOf(reply.text), username: "admin", password: "wrong-password" });
      const codes: number[] = [];
      for (let i = 0; i < 10; i++) codes.push((await post("/login", wrong, form)).status);
      expect(codes).toEqual([...Array(9).fill(401), 429]);
      const limited = await post("/login", wrong, { ...form, "X-Forwarded-For": "203.0.113.9" });
      expect([limited.status, limited.last("Retry-After")]).toEqual([429, "60"]);
    });

    await running(binary, { workers: 1, env: { ...environment, NEO_TRUSTED_PROXIES: "127.0.0.1" } }, async ({ port }) => {
      expect((await request(port, "/dashboard", { headers: { Cookie: cookie } })).status).toBe(303);
      const [fresh] = signed("admin", key, Date.now());
      const page = await request(port, "/dashboard", { headers: { Cookie: "neokit_session=" + fresh } });
      expect(page.status).toBe(200);
      for (const id of ids) expect([id, page.text.includes(id)]).toEqual([id, true]);
      expect((await request(port, "/login", { method: "POST", body: wrong, headers: form })).status).toBe(429);
      expect((await request(port, "/login", { method: "POST", body: wrong, headers: { ...form, "X-Forwarded-For": "203.0.113.9" } })).status).toBe(401);
    });
  });
});
