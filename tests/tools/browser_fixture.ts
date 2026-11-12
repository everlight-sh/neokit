// Local native applications for actual-browser checks. Ctrl-C stops all servers.
//
// All credentials and data here are disposable test fixtures, never user accounts.
// The control file under build/browser-check records URLs and editable dev sources.
//
//   bun tests/tools/browser_fixture.ts
//
// Expects build/studio, build/features and build/dashboard to be built already
// (as `./neokit test` leaves them); builds and serves its own lab application,
// and a `neokit dev` server for it.
import { pbkdf2Sync } from "node:crypto";
import { closeSync, mkdirSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Interrupted, ROOT, Supervised, freePort, interruption, jsonDumps, literal, neokit, running, sleep, writeTree } from "../lib";

function fixture(app: string) {
  const files: Record<string, string> = {};
  const write = (name: string, text: string) => { files[name] = text; };
  write("web.toml", '[site]\nname="Browser verification lab"\n[server]\naccess_log=true\n');
  write("app/layout.view", '@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }}</title><link rel="stylesheet" href="/site.css"></head><body><main id="web-page" tabindex="-1">{{ content }}</main>{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>');
  write("app/lab/layout.view", '@props(title:text,content:html,client:bool,mode:text)\n<aside data-web-persist="sidebar"><strong>Shared layout</strong><label>Sidebar draft<input id="sidebar-draft" value="original"></label><nav><a href="/lab/one" data-web-link data-web-prefetch>Page one</a> <a href="/lab/two" data-web-link data-web-prefetch>Page two</a></nav></aside><section>{{ content }}</section>');
  for (const [name, title] of [["one", "Page one"], ["two", "Page two"]]) {
    const page = join("app/lab", name);
    write(join(page, "page.toml"), 'loader="lab_load_' + name + '"\nclient=true\nexport=true\ncache_ttl=30\n[metadata]\ndescription="' + title + ' description"\n');
    write(join(page, "page.neo"), "import " + literal(join(ROOT, "src/lib.neo")) + "\ntype LabProps_" + name + "={title:Arr u8 _}\nfn lab_load_" + name + "(request:WebRequest)->LabProps_" + name + "=LabProps_" + name + " {title=" + literal(title) + "}\n");
    write(join(page, "page.view"), '@props(title:text)\n<h1>{{ title }}</h1><p data-version>Template version one</p><label>Page draft<input id="page-draft" value=""></label><div data-web-widget="lab" data-web-key="browser-lab" data-web-props=\'{"initial":3}\'><p>Count: <output data-count>3</output></p><button data-increment>Add one</button><label>Action title<input data-title value="valid"></label><button data-action>Save action</button><p>Action: <output data-pending>idle</output></p><p data-notice role="status"></p><p data-lifecycle></p><p data-widget-version></p></div>');
  }
  write("app/actions/test/route.toml", 'handler="lab_action"\ninput_type="LabInput"\nmethods=["POST"]\n[input]\ntitle={type="text",min=1,max=100}\ncount={type="int",min=0,max=100}\nenabled="bool"\n');
  write("app/actions/test/route.neo", "import " + literal(join(ROOT, "src/lib.neo")) + '\nimport "std:debug"\ntype LabInput={title:Arr u8 _,count:i64,enabled:bool}\neffect fn lab_action(request:WebRequest,input:LabInput)->WebResponse=let slept=sleep_ms(400) in if str_equal(input.title,"reject") then web_status(web_json("{\\"error\\":\\"Rejected by validation\\"}"),422) else web_json("{\\"ok\\":true}")\n');
  const runtime = join(ROOT, "client/runtime.js");
  write("client/index.js", "import {start} from " + jsonDumps(runtime) + ';\nstart({widgets:{lab:()=>import("./widget.js")}});\n');
  write("client/widget.js", `export function mount(element,context){
  const count=context.state('count',context.props.initial),mounts=context.state('mounts',0),cleans=context.state('cleans',0);
  mounts.set(v=>v+1);
  const stops=[count.subscribe(v=>element.querySelector('[data-count]').textContent=v,{immediate:true})];
  element.querySelector('[data-lifecycle]').textContent=\`Mounts \${mounts.get()} · Cleanups \${cleans.get()}\`;
  element.querySelector('[data-widget-version]').textContent='Widget version one';
  const increment=()=>count.set(v=>v+1);
  const action=context.action('/actions/test',{csrf:'test-fixture'});
  const save=()=>action.run({title:element.querySelector('[data-title]').value,count:42,enabled:true},{optimistic:()=>{const old=count.get();count.set(v=>v+1);return()=>count.set(old);}}).then(()=>element.querySelector('[data-notice]').textContent='Saved successfully').catch(error=>element.querySelector('[data-notice]').textContent=error.message);
  stops.push(action.pending.subscribe(v=>element.querySelector('[data-pending]').textContent=v?'pending':'idle',{immediate:true}));
  element.querySelector('[data-increment]').addEventListener('click',increment);element.querySelector('[data-action]').addEventListener('click',save);
  return()=>{cleans.set(v=>v+1);for(const stop of stops)stop();element.querySelector('[data-increment]').removeEventListener('click',increment);element.querySelector('[data-action]').removeEventListener('click',save);};
}
`);
  write("public/site.css", "body{font:17px system-ui;max-width:850px;margin:40px auto;padding:20px;color:#172033;background:#f5f7fa}aside,section{background:white;border:1px solid #d9dfe8;border-radius:12px;padding:24px;margin-bottom:18px}label{display:block;margin:12px 0}input,button{font:inherit;padding:8px;margin:6px}button,a{cursor:pointer}a{margin-right:20px}#neokit-dev-error{background:#421620;color:white;padding:24px;white-space:pre-wrap}");
  writeTree(app, files);
}

async function main(interrupted: Promise<never>) {
  process.env.NEOKIT_LIBRARY_CACHE = "off";
  const directory = join(ROOT, "build/browser-check");
  mkdirSync(directory, { recursive: true });
  const app = join(directory, "app");
  fixture(app);
  const release = join(directory, "release");
  neokit(["build", "--app", app, "--out", release]);
  const data = join(directory, "data");
  mkdirSync(data, { recursive: true });
  const cache = join(directory, "cache");
  mkdirSync(cache, { recursive: true });
  const key = "browser-test-session-secret-at-least-32-bytes";
  const password = "browser-test-password", salt = Buffer.from("browser-test-salt");
  const b64 = (value: Uint8Array) => Buffer.from(value).toString("base64url");
  const encoded = "p1.600000." + b64(salt) + "." + b64(pbkdf2Sync(password, salt, 600000, 32, "sha256"));
  await running(join(ROOT, "build/studio/server"), {}, async ({ port: studio }) =>
    running(join(ROOT, "build/features/server"), {}, async ({ port: features }) => {
      let number = await freePort();
      const origin = `http://127.0.0.1:${number}`;
      return running(join(ROOT, "build/dashboard/server"), { workers: 2, env: { NEOKIT_DATA_DIR: data, NEOKIT_SESSION_SECRET: key, NEOKIT_ORIGIN: origin, NEOKIT_ADMIN_PASSWORD_HASH: encoded }, port: number }, async ({ port: dashboard }) => {
        number = await freePort();
        return running(join(release, "server"), { env: { NEOKIT_ORIGIN: `http://127.0.0.1:${number}`, NEOKIT_CACHE_DIR: cache }, port: number }, async ({ port: lab }) => {
          const devport = await freePort();
          const devlog = openSync(join(directory, "dev.log"), "w");
          const dev = new Supervised(join(ROOT, "neokit"), ["dev", "--app", app, "--out", join(directory, "dev"), "--port", String(devport)], {
            env: { ...process.env, NEOKIT_ORIGIN: `http://127.0.0.1:${devport}` }, stdout: devlog, stderr: devlog,
          });
          try {
            await dev.started;
            const info = { studio: `http://127.0.0.1:${studio}`, features: `http://127.0.0.1:${features}`, dashboard: `http://127.0.0.1:${dashboard}`, lab: `http://127.0.0.1:${lab}`,
              dev: `http://127.0.0.1:${devport}`, app, username: "admin", password };
            writeFileSync(join(directory, "control.json"), jsonDumps(info, { indent: 2 }) + "\n");
            console.log(jsonDumps(info));
            while (dev.running) await Promise.race([interrupted, sleep(1000)]);
            throw new Error("Development server stopped: " + readFileSync(join(directory, "dev.log"), "utf8"));
          } finally {
            await dev.stop({ signal: "SIGINT", group: false, grace: 15000 });
            closeSync(devlog);
          }
        });
      });
    }));
}

const interrupted = interruption();
try {
  await main(interrupted);
} catch (error) {
  if (!(error instanceof Interrupted)) throw error;
}
process.exit(0);
