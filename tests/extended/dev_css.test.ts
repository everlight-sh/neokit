// Actual `neokit dev` restarts for Tailwind template, imported CSS and JS
// edits: template utilities, imported CSS, browser-only classes, disk-served
// assets, a failed build keeps the previous assets, a repair refreshes them.
import { test, expect } from "bun:test";
import { closeSync, openSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, Supervised, freePort, isNetworkError, literal, request, sleep, withTemporary, writeTree } from "../lib";

/** A failed check that waitFor may retry. */
class Unready extends Error {}

test("dev watcher: Tailwind template, imported CSS and JS edits, failed and repaired builds", async () => {
  await withTemporary("neokit-tailwind-dev-", async work => {
    const app = join(work, "app");
    writeTree(app, {
      "web.toml": '[css]\ntailwind=["site.css"]\n',
      "app/layout.view": '@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html><head><title>{{ title }}</title><link rel="stylesheet" href="@asset(site.css)"></head><body>{{ content }}</body></html>',
      "app/page.toml": 'loader="load"\nmode="ssr"\nclient=false\n',
      "app/page.neo": "import " + literal(join(ROOT, "src/lib.neo")) + "\ntype Props={title:Arr u8 _}\nfn load(request:WebRequest)->Props=Props {title=\"Live Tailwind\"}\n",
      "app/page.view": '@props(title:text)\n<h1 class="p-4">{{ title }}</h1>',
      "styles/site.css": '@import "tailwindcss" source(none);\n@import "./detail.css";\n@source "../app";\n@source "../client";\n',
      "styles/detail.css": ".import-test { color: #123456; }",
      "client/index.js": 'document.documentElement.classList.add("opacity-50");',
    });
    symlinkSync(join(ROOT, "node_modules"), join(app, "node_modules"), "dir");
    const view = join(app, "app/page.view"), css = join(app, "styles/site.css"), imported = join(app, "styles/detail.css"), js = join(app, "client/index.js");

    const port = await freePort();
    const logPath = join(work, "dev.log");
    const logFile = openSync(logPath, "w");
    const dev = new Supervised(join(ROOT, "neokit"), ["dev", "--app", app, "--out", join(work, "output"), "--port", String(port)],
                              { cwd: ROOT, stdout: logFile, stderr: logFile });
    closeSync(logFile);
    const log = () => readFileSync(logPath, "utf8");
    try {
      // Content-Length must be present and exact; a mismatch may be retried.
      const get = async (path = "/") => {
        const reply = await request(port, path, { timeout: 10000, keepAlive: true });
        if (!reply.has("Content-Length")) throw new Error(`no Content-Length on ${path}`);
        if (Number(reply.header("Content-Length")) !== reply.body.length) throw new Unready(`Content-Length mismatch on ${path}`);
        return reply;
      };
      const pageCss = async (): Promise<[Buffer, string, Buffer]> => {
        const page = await get();
        if (page.status !== 200) throw new Unready(`page status ${page.status}`);
        const link = /href="(\/_web\/site[^"]*\.css)"/.exec(page.body.toString("latin1"));
        if (!link) throw new Error("no site.css link in the page:\n" + page.text.slice(0, 500));
        const sheet = await get(link[1]);
        if (sheet.status !== 200) throw new Unready(`stylesheet status ${sheet.status}`);
        return [page.body, link[1], sheet.body];
      };
      // Poll every 100 ms for up to 30 s; connection failures and Unready retry, anything else fails.
      const waitFor = async (check: () => Promise<boolean>) => {
        const end = performance.now() + 30000;
        while (performance.now() < end) {
          if (!dev.running) throw new Error("neokit dev exited:\n" + log());
          try {
            if (await check()) return;
          } catch (error) {
            if (!(error instanceof Unready) && !isNetworkError(error)) throw error;
          }
          await sleep(100);
        }
        throw new Error("timed out:\n" + log());
      };

      await waitFor(async () => (await pageCss())[2].includes(".p-4{"));
      // Development keeps stable asset URLs and serves them from disk with
      // no-store, so CSS-only edits never recompile the server.
      const [, url] = await pageCss();
      expect(url).toBe("/_web/site.css");
      expect((await get(url)).last("Cache-Control")).toBe("no-store");

      writeFileSync(view, '@props(title:text)\n<h1 class="grid grid-cols-3">{{ title }}</h1>');
      await waitFor(async () => (await pageCss())[0].includes('class="grid grid-cols-3"') && (await pageCss())[2].includes(".grid-cols-3{"));
      writeFileSync(imported, ".import-test { color: #654321; }");
      await waitFor(async () => (await pageCss())[2].includes("#654321"));
      writeFileSync(js, 'document.documentElement.classList.add("opacity-60");');
      await waitFor(async () => (await pageCss())[2].includes(".opacity-60{") && !(await pageCss())[2].includes(".opacity-50{"));

      const stable = (await pageCss())[2];
      writeFileSync(css, '@import "./missing.css";');
      await waitFor(async () => log().includes("Tailwind failed"));
      expect((await pageCss())[2].equals(stable)).toBe(true);
      writeFileSync(css, '@import "tailwindcss" source(none);\n@import "./detail.css";\n@source "../app";\n@source "../client";\n@source inline("underline");\n');
      await waitFor(async () => (await pageCss())[2].includes(".underline{"));
    } finally {
      // SIGINT to neokit dev itself, not its group; killed after 10 s.
      await dev.stop({ signal: "SIGINT", group: false, grace: 10000 });
    }
  });
});
