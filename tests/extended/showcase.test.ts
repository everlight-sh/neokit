// Independent HTTP semantics for the three complete showcase applications:
// fresh SSR and render timing, blog search/articles/escaping, computed charts
// and API, 10k-row pagination and filtering, CSV export and activity.
import { test, expect } from "bun:test";
import { join } from "node:path";
import { ROOT, countOf, neokit, request, running, sleep } from "../lib";

const NAMES = ["ssr-site", "blog", "saas"];
const binaries = Object.fromEntries(NAMES.map(name => {
  const out = join(ROOT, "build/showcase", name);
  neokit(["build", "--app", join(ROOT, "examples", name), "--out", out]);
  return [name, join(out, "server")];
}));

/** GET (or HEAD) with a 20 s timeout: no server error, a Server-Timing render
 *  duration, and an exact Content-Length. Headers are read last-value-wins. */
async function get(port: number, path: string, method = "GET") {
  const reply = await request(port, path, { method, timeout: 20000, keepAlive: true });
  expect([path, reply.status < 500, reply.status < 500 ? "" : reply.body.subarray(0, 200).toString()]).toEqual([path, true, ""]);
  expect([path, /^render;dur=\d+\.\d{3}$/.test(reply.last("server-timing") ?? "")]).toEqual([path, true]);
  if (method !== "HEAD") expect([path, Number(reply.last("content-length") ?? NaN)]).toEqual([path, reply.body.length]);
  return reply;
}
const ids = (body: Buffer) => [...body.toString("latin1").matchAll(/data-customer-id="(\d+)"/g)].map(match => match[1]);
const grouped = (n: number) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** csv.DictReader over the default (excel) dialect. */
function csvRecords(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [], field = "", quoted = false, inField = false, i = 0;
  const endField = () => { row.push(field); field = ""; inField = false; };
  const endRow = () => { if (inField || row.length) endField(); if (row.length) rows.push(row); row = []; };
  while (i < text.length) {
    const char = text[i];
    if (quoted) {
      if (char === '"') { if (text[i + 1] === '"') { field += '"'; i += 2; continue; } quoted = false; i++; continue; }
      field += char; i++; continue;
    }
    if (char === '"' && !inField) { quoted = true; inField = true; i++; continue; }
    if (char === ",") { endField(); inField = true; i++; continue; }
    if (char === "\r" || char === "\n") { endRow(); i += char === "\r" && text[i + 1] === "\n" ? 2 : 1; continue; }
    field += char; inField = true; i++;
  }
  if (quoted) throw new Error("csv: unexpected end of data inside a quoted field");
  endRow();
  const [header, ...records] = rows;
  return records.map(values => Object.fromEntries(header.map((name, index) => [name, values[index]])));
}

test("ssr-site: fresh SSR, render timing, pages, assets and HEAD", async () => {
  await running(binaries["ssr-site"], {}, async ({ port }) => {
    const first = await get(port, "/");
    await sleep(5);
    const second = await get(port, "/");
    expect([first.status, first.text.includes("Good spaces."), first.text.includes("Courtyard House")]).toEqual([200, true, true]);
    const stamp = (reply: Buffer) => Number(/<code>(\d+)<\/code>/.exec(reply.toString("latin1"))![1]);
    expect(stamp(second.body)).toBeGreaterThan(stamp(first.body));
    expect(first.last("cache-control")).toContain("no-store");
    expect((await get(port, "/studio")).text).toContain("A small studio.");
    expect((await get(port, "/courtyard.svg")).last("content-type")!.startsWith("image/svg")).toBe(true);
    expect((await get(port, "/", "HEAD")).body.length).toBe(0);
  });
});

test("blog: listing, category and search filters, escaping, articles", async () => {
  await running(binaries.blog, {}, async ({ port }) => {
    expect(countOf((await get(port, "/")).body, 'class="story-card"')).toBe(3);
    const filtered = (await get(port, "/?category=engineering")).body;
    expect([countOf(filtered, 'class="story-card"'), filtered.includes("Make room for a smaller web")]).toEqual([1, true]);
    expect(countOf((await get(port, "/?q=mornings")).body, 'class="story-card"')).toBe(1);
    const hostile = (await get(port, "/?q=%22%3E%3Cscript%3Ebad%3C/script%3E")).body;
    expect([hostile.includes("<script>bad"), hostile.includes("&lt;script&gt;")]).toEqual([false, true]);
    expect((await get(port, "/?q=not-a-real-story")).text).toContain("No stories found");
    for (const slug of ["a-smaller-web", "slower-mornings", "quiet-interfaces"]) {
      const article = await get(port, "/journal/" + slug);
      expect([slug, article.status, countOf(article.body, "<section><h2>")]).toEqual([slug, 200, 4]);
    }
    expect((await get(port, "/journal/missing")).status).toBe(404);
  });
});

test("saas: computed charts and API, 10k pagination and filtering, CSV export, activity", async () => {
  await running(binaries.saas, {}, async ({ port }) => {
    for (const days of [7, 30, 90]) {
      const page = await get(port, `/?range=${days}`);
      expect([days, page.status, countOf(page.body, "data-chart-value=")]).toEqual([days, 200, days]);
      const api = (await get(port, `/api/metrics?range=${days}`)).json();
      const expected = Array.from({ length: days }, (_, i) => 1400 + i * 31 + ((i * 79 + days * 17) % 600));
      expect(api.demo).toBeTruthy();
      expect([api.revenue, api.period_days, api.records]).toEqual([expected, days, 1000]);
      expect(page.text).toContain("$" + grouped(expected.reduce((sum, value) => sum + value, 0)));
    }
    const large = (await get(port, "/customers?records=10000")).body;
    expect([countOf(large, "data-customer-id="), large.includes("10,000")]).toEqual([25, true]);
    const one = new Set(ids(large));
    const two = ids((await get(port, "/customers?records=10000&page=2")).body);
    expect(two.filter(id => one.has(id))).toEqual([]);
    const last = (await get(port, "/customers?records=10000&page=400")).body;
    expect([last.includes('data-customer-id="10000"'), countOf(last, "data-customer-id=")]).toEqual([true, 25]);
    const matching = ids((await get(port, "/customers?q=Orbit&plan=Pro")).body).map(Number);
    expect(matching.length).toBeGreaterThan(0);
    expect(matching.filter(i => !((i - 1) % 12 === 1 && (i - 1) % 9 >= 5 && (i - 1) % 9 < 8))).toEqual([]);
    expect((await get(port, "/customers?q=unmatched-workspace")).text).toContain("No matching customers");
    const exported = await get(port, "/api/export-customers?q=Orbit&plan=Pro");
    const records = csvRecords(new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(exported.body));
    expect(records.length).toBeGreaterThan(0);
    for (const record of records) expect([record.company.includes("Orbit"), record.plan]).toEqual([true, "Pro"]);
    expect(exported.last("content-disposition")).toContain("attachment;");
    expect(countOf((await get(port, "/activity")).body, 'class="event"')).toBe(18);
  });
});
