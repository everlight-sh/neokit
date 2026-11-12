// [security] csp on streamed responses. Streamed handlers return the head Neo
// wrote, so the generated server puts the site policy in it
// (neokit_stream_policy in src/security.neo). One app, built with and without a
// policy: an async page (web_stream_page, async=true), a raw stream, a raw
// stream with its own policy, their 405 replies and an event stream.
import { test, expect } from "bun:test";
import { join } from "node:path";
import { EventStream, ROOT, check, exchange, literal, neokit, parseResponses, request, running, withTemporary, writeTree } from "../lib";

const NEO_DEFAULT = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'";
const CSP = "default-src 'self'; img-src 'self' https://cdn.example";
const OWN = "default-src 'none'";
const LIB = "import " + literal(join(ROOT, "src/lib.neo")) + "\n";
const STREAM = "import " + literal(join(ROOT, "src/stream.neo")) + "\n";
const REALTIME = "import " + literal(join(ROOT, "src/realtime.neo")) + "\n";
const FILES: Record<string, string> = {
  "web.toml": '[site]\nname="Stream policy"\n',
  "app/layout.view": '@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html><head><meta charset="utf-8"><title>{{ title }}</title></head><body>{{ content }}</body></html>',
  "app/page.toml": 'loader="policy_home"\nclient=false\nexport=false\n',
  "app/page.neo": LIB + 'type HomeProps = { title:Arr u8 _ }\nfn policy_home(request:WebRequest) -> HomeProps = HomeProps { title="Home" }\n',
  "app/page.view": "@props(title:text)\n<h1>{{ title }}</h1>",
  "app/async/route.toml": 'handler="policy_async"\nstream=true\nasync=true\nasync_timeout_ms=5000\n',
  "app/async/route.neo": STREAM + `private effect(fs,net,env,time,process) fn policy_part(capture:Arr u8 n)->Arr u8 _ = concat("<p>",html_escape(capture),"</p>")
effect fn policy_async(request:WebRequest)->Arr u8 _
  = let part=web_defer("part",web_html("<p>Loading</p>"),"Deferred part",function(policy_part)) in let deadline=deadline_after(5000) in
    web_stream_page("Policy",web_html(concat("<h1>Streamed</h1>",web_boundary(part).bytes)),arr(i in 0..1) part,deadline.value)
`,
  "app/raw/route.toml": 'handler="policy_raw"\nstream=true\n',
  "app/raw/route.neo": STREAM + `private effect(fs,net,env,time,process) fn policy_raw_part(capture:Arr u8 n)->Arr u8 _ = "<p>Raw ready</p></body></html>"
effect fn policy_raw(request:WebRequest)->Arr u8 _
  = let deadline=deadline_after(5000) in
    let begin=http_render_start_at(web_html("<!doctype html><html><body><p>Raw shell</p>"),arr(i in 0..1) http_deferred("",function(policy_raw_part)),deadline.value,"",request.stamp) in
    if begin.ok then begin.value else web_encode_at(web_reply(503,"Async capacity unavailable","text/plain; charset=utf-8"),false,request.stamp)
`,
  "app/own/route.toml": 'handler="policy_own"\nstream=true\n',
  "app/own/route.neo": STREAM + `private effect(fs,net,env,time,process) fn policy_own_part(capture:Arr u8 n)->Arr u8 _ = "<p>Own ready</p></body></html>"
effect fn policy_own(request:WebRequest)->Arr u8 _
  = let deadline=deadline_after(5000) in
    let begin=http_render_start_at(web_html("<!doctype html><html><body><p>Own shell</p>"),arr(i in 0..1) http_deferred("",function(policy_own_part)),deadline.value,
      "Content-Security-Policy: ${OWN}\\r\\n",request.stamp) in
    if begin.ok then begin.value else web_encode_at(web_reply(503,"Async capacity unavailable","text/plain; charset=utf-8"),false,request.stamp)
`,
  "app/events/route.toml": 'handler="policy_events"\nrealtime=true\nmethods=["GET"]\n',
  "app/events/route.neo": REALTIME + 'effect fn policy_events(request:WebRequest)->Arr u8 _=web_subscribe(request,web_channel("policy"))\n',
  "app/publish/route.toml": 'handler="policy_publish"\nmethods=["POST"]\n',
  "app/publish/route.neo": REALTIME + `effect fn policy_publish(request:WebRequest)->WebResponse
  = let published=web_publish(web_channel("policy"),"message",request.body) in if !published.ok then web_reply(400,"Invalid event","text/plain") else web_json("{}")
`,
};
const DATE = /^Date: [A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/;

/** One GET read raw until the server closes: its head's lines as they arrived, and the parsed reply. */
async function streamed(port: number, path: string) {
  const raw = await exchange(port, Buffer.from(`GET ${path} HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\n\r\n`, "latin1"));
  const end = raw.indexOf("\r\n\r\n");
  check(end > 0, raw.toString("latin1").slice(0, 300));
  check(raw.subarray(-5).equals(Buffer.from("0\r\n\r\n")), raw.subarray(-200).toString("latin1"));
  return { lines: raw.subarray(0, end).toString("latin1").split("\r\n"), reply: parseResponses(raw, ["GET"])[0] };
}

/** A streamed HTML head exactly as std:http_async writes it, Date included, with `policy` as its only policy. */
function expectHead(path: string, lines: string[], policy: string) {
  expect([path, lines.map(line => DATE.test(line) ? "Date: (now)" : line)]).toEqual([path, ["HTTP/1.1 200 OK", "Content-Type: text/html; charset=utf-8",
    "Cache-Control: no-store", "Date: (now)", "X-Content-Type-Options: nosniff", "Content-Security-Policy: " + policy, "Transfer-Encoding: chunked", "Connection: close"]]);
}

async function checkPolicies(csp: string | undefined) {
  await withTemporary("neokit-stream-policy-", async work => {
    const app = join(work, "app");
    writeTree(app, csp ? { ...FILES, "web.toml": FILES["web.toml"] + `\n[security]\ncsp="${csp}"\n` } : FILES);
    const out = join(work, "server");
    neokit(["build", "--app", app, "--out", out]);
    const policy = csp ?? NEO_DEFAULT;
    await running(join(out, "server"), {}, async ({ port }) => {
      // A buffered page has the same policy.
      expect((await request(port, "/")).all("Content-Security-Policy")).toEqual([policy]);
      // The async page: the site policy replaces Neo's default; Date and the chunked body stay.
      const page = await streamed(port, "/async");
      expectHead("/async", page.lines, policy);
      for (const text of ["<h1>Streamed</h1>", "<p>Deferred part</p>", "data-web-stream-end="]) expect(page.reply.text).toContain(text);
      const raw = await streamed(port, "/raw");
      expectHead("/raw", raw.lines, policy);
      expect(raw.reply.text).toBe("<!doctype html><html><body><p>Raw shell</p><p>Raw ready</p></body></html>");
      // A stream's own policy stays.
      const own = await streamed(port, "/own");
      expectHead("/own", own.lines, OWN);
      expect(own.reply.text).toContain("<p>Own ready</p>");
      // 405 replies from the async and the plain stream dispatch.
      for (const path of ["/async", "/raw"]) {
        const refused = await request(port, path, { method: "POST" });
        expect([path, refused.status, refused.all("Content-Security-Policy")]).toEqual([path, 405, [policy]]);
      }
      // Neo's event-stream head has no policy; the site's is added before its blank line, and events still flow.
      const events = await EventStream.open(port, "/events");
      try {
        expect(events.reply.all("Content-Security-Policy")).toEqual(csp ? [csp] : []);
        expect((await events.next()).event).toBe("neokit-ready");
        expect((await request(port, "/publish", { method: "POST", body: '{"n":1}' })).status).toBe(200);
        const message = await events.message();
        expect([message.event, message.data]).toEqual(["message", { n: 1 }]);
      } finally { events.close(); }
    });
  });
}

test("streamed responses carry [security] csp", () => checkPolicies(CSP));
test("streamed responses keep Neo's default policy without [security] csp", () => checkPolicies(undefined));
