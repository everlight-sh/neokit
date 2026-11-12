// Compile and exercise the routing, template, caching and metadata options
// added after the 2026-11 audit in one native application and its export.
import { test, expect } from "bun:test";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { ROOT, countOf, literal, neokit, request, running, withTemporary, writeTree } from "../lib";

const LIB = "import " + literal(join(ROOT, "src/lib.neo")) + "\n";
const CSP = "default-src 'self'; img-src 'self' https://cdn.example";
const FILES: Record<string, string> = {
  "web.toml": `[site]
url="https://audit.test"
description="Audit site"

[[redirects]]
from="/old"
to="/pricing"

[[redirects]]
from="/docs-external"
to="https://docs.example/start"
status=301
query=false

[security]
csp="default-src 'self'; img-src 'self' https://cdn.example"
`,
  "app/api/own-policy/route.toml": 'handler="audit_own_policy"\n',
  "app/api/own-policy/route.neo": LIB + `fn audit_own_policy(request:WebRequest) -> WebResponse = web_with_header(web_json("{}"),"Content-Security-Policy","default-src 'none'")\n`,
  "app/layout.view": '@props(title:text,content:html,client:bool,mode:text,head:html)\n<!doctype html><html><head><title>{{ title }}</title>{{ head }}</head><body><main id="web-page">{{ content }}</main></body></html>',
  "app/not-found.view": "@props()\n<!doctype html><title>Missing</title><h1>Nothing here</h1>",
  "app/page.toml": 'loader="audit_home"\n[metadata.props]\ndescription="summary"\njsonld="jsonld"\n',
  "app/page.neo": LIB + String.raw`type Post = { title:Arr u8 _, href:WebURL, views:i64 }
type HomeProps = { title:Arr u8 _, query:Arr u8 _, posts:Arr Post _, summary:Arr u8 _, jsonld:Arr u8 _ }
fn audit_home(request:WebRequest) -> HomeProps
  = HomeProps { title="Home", query=web_query_get(request.query,"q").value,
      posts=arr(i in 0..3) Post { title=if i==0 then "First" else if i==1 then "Second" else "Third",
        href=web_url(if i==0 then "/blog/a" else "/blog/b"), views=if i==0 then 5000 else if i==1 then 0 else 7 },
      summary="Home <page> & more", jsonld="{\"@type\":\"WebSite\",\"name\":\"</script><b>x</b>\"}" }
`,
  "app/page.view": "@props(title:text, query:text, posts:Post[], summary:text, jsonld:text)\n@type Post(title:text, href:url, views:int)\n"
    + '<h1>{{ title }}</h1>{% if query %}<p class="q">Results for {{ query }}</p>{% else %}<p class="q">No query</p>{% endif %}'
    + '<ul>{% for post, index in posts %}<li data-rank="{{ index }}"><a href="{{ post.href }}">{{ post.title }}</a>'
    + "{% if post.views >= 1000 %} popular{% elif post.views == 0 %} new{% else %} normal{% endif %}</li>{% endfor %}</ul>"
    + '{% if query == "neo" %}<p>exact</p>{% endif %}',
  "app/(marketing)/pricing/page.toml": 'loader="audit_pricing"\nmode="static"\n',
  "app/(marketing)/pricing/page.neo": LIB + 'type PricingProps = { title:Arr u8 _ }\nfn audit_pricing(request:WebRequest) -> PricingProps = PricingProps { title="Pricing" }\n',
  "app/(marketing)/pricing/page.view": "@props(title:text)\n<h1>{{ title }}</h1>",
  "app/blog/[slug]/page.toml": 'loader="audit_post"\nmode="static"\npaths_fn="audit_slugs"\n',
  "app/blog/[slug]/page.neo": LIB + `type PostProps = { title:Arr u8 _, slug:Arr u8 _ }
fn audit_post(request:WebRequest) -> PostProps = PostProps { title="Post", slug=web_param(request,"slug").value }
fn audit_slugs() -> Arr Arr u8 _ _ = arr(i in 0..2) if i==0 then "/blog/a" else "/blog/b"
`,
  "app/blog/[slug]/page.view": "@props(title:text, slug:text)\n<h1>Post {{ slug }}</h1>",
  "app/account/page.toml": 'loader="audit_account"\nguard="audit_guard"\n',
  "app/account/page.neo": LIB + `type AccountProps = { title:Arr u8 _ }
fn audit_account(request:WebRequest) -> AccountProps = AccountProps { title="Account" }
fn audit_guard(request:WebRequest) -> WebResponse
  = let user=web_request_header(request,"x-user") in
    if str_equal(user,"ghost") then web_reply(404,"","text/plain; charset=utf-8")
    else if len(user)==0 then web_redirect("/login",303) else web_continue()
`,
  "app/account/page.view": "@props(title:text)\n<h1>{{ title }}</h1>",
  "app/search/page.toml": 'loader="audit_search"\ncache_ttl=60\ncache_query=["q"]\n',
  "app/search/page.neo": LIB + `type SearchProps = { title:Arr u8 _, q:Arr u8 _, stamp:i64 }
fn audit_search(request:WebRequest) -> SearchProps = SearchProps { title="Search", q=web_query_get(request.query,"q").value, stamp=request.stamp }
`,
  "app/search/page.view": "@props(title:text, q:text, stamp:int)\n<p>{{ q }}:{{ stamp }}</p>",
  "app/private-info/page.toml": 'loader="audit_private"\ncache_control="private, max-age=30"\n',
  "app/private-info/page.neo": LIB + 'type PrivateProps = { title:Arr u8 _ }\nfn audit_private(request:WebRequest) -> PrivateProps = PrivateProps { title="Private" }\n',
  "app/private-info/page.view": "@props(title:text)\n<h1>{{ title }}</h1>",
  "app/feed.xml/route.toml": 'handler="audit_feed"\n',
  "app/feed.xml/route.neo": LIB + 'fn audit_feed(request:WebRequest) -> WebResponse = web_reply(200,"<rss/>","application/rss+xml")\n',
  "app/api/data/route.toml": 'handler="audit_data"\nmethods=["GET","POST"]\ncors=["https://app.example"]\ncors_headers=["content-type","x-token"]\ncsrf=false\n',
  "app/api/data/route.neo": LIB + 'fn audit_data(request:WebRequest) -> WebResponse = web_json("{\\"ok\\":true}")\n',
};

const isFile = (path: string) => existsSync(path) && statSync(path).isFile();

test("audit features", async () => {
  await withTemporary("neokit-audit-", async work => {
    const app = join(work, "app");
    writeTree(app, FILES);
    const out = join(work, "server");
    neokit(["build", "--app", app, "--out", out]);
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf8"));
    expect(manifest.redirects).toContainEqual({ source: "/old", target: "/pricing", status: 308 });
    expect(manifest.routes.filter((route: any) => route.path === "/blog/[slug]")[0].paths).toEqual(["/blog/a", "/blog/b"]);

    await withTemporary("neokit-audit-cache-", cache => running(join(out, "server"), { env: { NEOKIT_CACHE_DIR: cache } }, async ({ port }) => {
      // Every reply declares its exact Content-Length; headers are read last-value-wins (.last()).
      const get = (path: string, headers?: Record<string, string>, method = "GET") => request(port, path, { method, headers, requireLength: true });
      const home = await get("/");
      expect(home.status).toBe(200);
      expect(home.text).toContain('<p class="q">No query</p>');
      for (const [rank, [title, label]] of ([["First", " popular"], ["Second", " new"], ["Third", " normal"]] as const).entries())
        expect(home.text).toContain(`<li data-rank="${rank}"><a href="/blog/${rank === 0 ? "a" : "b"}">${title}</a>${label}`);
      expect(home.text).toContain('<meta name="description" content="Home &lt;page&gt; &amp; more">');
      expect(home.text).not.toContain("Audit site");
      expect(home.text).toContain('<script type="application/ld+json">{"@type":"WebSite","name":"\\u003c/script>\\u003cb>x\\u003c/b>"}</script>');
      expect(home.text).not.toContain("</script><b>");
      expect(countOf(home.text, "</head>")).toBe(1);
      const searched = (await get("/?q=neo")).text;
      expect(searched).toContain("Results for neo");
      expect(searched).toContain("<p>exact</p>");
      expect((await get("/?q=other")).text).not.toContain("<p>exact</p>");

      const pricing = await get("/pricing");
      expect(pricing.status).toBe(200);
      expect(pricing.text).toContain("<h1>Pricing</h1>");
      expect(pricing.last("Cache-Control")).toBe("public, no-cache");
      expect(pricing.last("ETag")!.startsWith('W/"')).toBe(true);
      expect((await get("/(marketing)/pricing")).status).toBe(404);
      for (const slug of ["a", "b"]) expect(countOf((await get("/blog/" + slug)).text, `<h1>Post ${slug}</h1>`)).toBe(1);
      const missing = await get("/blog/c");
      expect(missing.status).toBe(404);
      expect(missing.text).toContain("Nothing here");

      const account = await get("/account");
      expect([account.status, account.last("Location")]).toEqual([303, "/login"]);
      const ghost = await get("/account", { "X-User": "ghost" });
      expect(ghost.status).toBe(404);
      expect(ghost.text).toContain("Nothing here");
      const allowed = await get("/account", { "X-User": "ann" });
      expect(allowed.status).toBe(200);
      expect(allowed.text).toContain("<h1>Account</h1>");

      const first = await get("/search?q=a");
      expect([first.last("X-NeoKit-Cache"), first.last("Cache-Control"), first.last("Vary")]).toEqual(["MISS", "public, max-age=60", "X-Web-Fragment"]);
      const again = await get("/search?q=a&utm=1");
      expect(again.last("X-NeoKit-Cache")).toBe("HIT");
      expect(again.body.equals(first.body)).toBe(true);
      expect((await get("/search?q=b")).last("X-NeoKit-Cache")).toBe("MISS");
      expect((await get("/search")).last("X-NeoKit-Cache")).toBe("MISS");
      expect((await get("/search?q=")).last("X-NeoKit-Cache")).toBe("MISS");
      const privateInfo = await get("/private-info");
      expect([privateInfo.last("Cache-Control"), privateInfo.last("Vary")]).toEqual(["private, max-age=30", "X-Web-Fragment"]);
      const feed = await get("/feed.xml");
      expect([feed.status, feed.text, feed.last("Content-Type")]).toEqual([200, "<rss/>", "application/rss+xml"]);

      const preflight = await get("/api/data", { Origin: "https://app.example", "Access-Control-Request-Method": "POST" }, "OPTIONS");
      expect(preflight.status).toBe(204);
      expect(preflight.last("Access-Control-Allow-Origin")).toBe("https://app.example");
      expect([...new Set((preflight.last("Access-Control-Allow-Methods") ?? "").split(", "))].sort()).toEqual(["GET", "HEAD", "POST"]);
      expect(preflight.last("Access-Control-Allow-Headers")).toBe("content-type, x-token");
      expect(preflight.last("Vary")).toBe("Origin");
      expect(preflight.last("Access-Control-Max-Age")).toBe("600");
      const refused = await get("/api/data", { Origin: "https://evil.example" }, "OPTIONS");
      expect(refused.status).toBe(204);
      expect(refused.has("Access-Control-Allow-Origin")).toBe(false);
      const data = await get("/api/data", { Origin: "https://app.example" });
      expect([data.status, data.json(), data.last("Access-Control-Allow-Origin")]).toEqual([200, { ok: true }, "https://app.example"]);
      const foreign = await get("/api/data", { Origin: "https://evil.example" });
      expect([foreign.status, foreign.has("Access-Control-Allow-Origin"), foreign.last("Vary")]).toEqual([200, false, "Origin"]);
      expect((await get("/api/data", undefined, "DELETE")).status).toBe(405);
      const old = await get("/old?x=1");
      expect([old.status, old.last("Location")]).toEqual([308, "/pricing?x=1"]);
      const external = await get("/docs-external?x=1");
      expect([external.status, external.last("Location")]).toEqual([301, "https://docs.example/start"]);

      for (const path of ["/", "/account", "/pricing", "/blog/c", "/api/data"]) {
        const reply = await request(port, path);
        expect([path, reply.all("Content-Security-Policy")]).toEqual([path, [CSP]]);
        expect([path, Boolean(reply.header("Date"))]).toEqual([path, true]);
      }
      expect((await request(port, "/api/own-policy")).all("Content-Security-Policy")).toEqual(["default-src 'none'"]);
      const sitemap = (await get("/sitemap.xml")).text;
      for (const path of ["/", "/pricing", "/blog/a", "/blog/b"]) expect(sitemap).toContain(`<loc>https://audit.test${path}</loc>`);
      expect(sitemap).not.toContain("/account");
    }));

    // target="static": the export CLI writes the snapshots under --out and copies them to --dest.
    const exported = join(work, "export");
    neokit(["export", "--app", app, "--out", exported, "--dest", join(work, "site")]);
    const staticManifest = JSON.parse(readFileSync(join(exported, "manifest.json"), "utf8"));
    const snapshots = join(exported, "snapshots");
    for (const page of ["index.html", "pricing/index.html", "blog/a/index.html", "blog/b/index.html", "404.html"])
      expect([page, isFile(join(snapshots, page))]).toEqual([page, true]);
    expect(existsSync(join(snapshots, "account"))).toBe(false);
    expect(staticManifest.omitted_server_routes).toContain("/account");
    expect(readFileSync(join(snapshots, "404.html"), "utf8")).toContain("Nothing here");
    expect(readFileSync(join(snapshots, "_redirects"), "utf8")).toBe("/old /pricing 308\n/docs-external https://docs.example/start 301\n");
    expect(readFileSync(join(snapshots, "_headers"), "utf8")).toBe("/*\n  Content-Security-Policy: " + CSP + "\n");
    expect(readFileSync(join(snapshots, "index.html"), "utf8")).toContain('name="description"');
  });
});
