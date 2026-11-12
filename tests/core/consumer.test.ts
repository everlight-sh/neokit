// Build a different, isolated application using the public framework contracts.
import { test, expect } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, literal, neokit, request, running, withTemporary, writeTree } from "../lib";

/** Escapes &, <, >, " and ' (as &#x27;) for HTML text and attributes. */
const escapeHtml = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");

test("isolated consumer", async () => {
  await withTemporary("neokit-consumer-", async workspace => {
    const app = join(workspace, "another-site");
    const title = 'Another <site> & "café"';
    writeTree(app, {
      "web.toml": '[site]\nname="Another site"\n',
      "app/layout.view": '@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html lang="en"><head><title>{{ title }}</title></head><body><main id="web-page">{{ content }}</main>{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>',
      "app/page.neo": "import " + literal(join(ROOT, "src/lib.neo")) + "\ntype OtherProps={title:Arr u8 _,count:i64,visible:bool}\nfn other_load(request:WebRequest)->OtherProps = OtherProps {title=" + literal(title) + ",count=42,visible=true}\n",
      "app/page.toml": 'loader="other_load"\nclient=false\nmode="ssr"\n',
      "app/page.view": '@props(title:text,count:int,visible:bool)\n<h1 title="{{ title }}">{{ title }}</h1>{% if visible %}<output>{{ count }}</output>{% endif %}',
    });
    // The CLI builds the server (build) and the static snapshots (export)
    // separately.
    const out = join(workspace, "output");
    neokit(["build", "--app", app, "--out", out]);
    const exported = join(workspace, "output-static");
    neokit(["export", "--app", app, "--out", exported, "--dest", join(workspace, "site")]);
    const manifest = JSON.parse(readFileSync(join(exported, "manifest.json"), "utf8"));
    const escaped = escapeHtml(title);

    await running(join(out, "server"), {}, async ({ port }) => {
      const reply = await request(port, "/", { requireLength: true });
      expect(reply.status).toBe(200);
      expect(reply.text).toContain(escaped);
      expect(reply.text).not.toContain("<site>");
      expect(reply.text).toContain("<output>42</output>");
      expect(reply.text).not.toContain("<script");
    });
    expect(manifest.exported_paths).toEqual(["/"]);
    expect(readFileSync(join(exported, "snapshots/index.html"), "utf8")).toContain(escaped);
    expect(readFileSync(join(workspace, "site/index.html"), "utf8")).toContain(escaped);
  });
});
