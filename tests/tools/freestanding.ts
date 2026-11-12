#!/usr/bin/env bun
// Serve the complete site/assets from a Linux root containing only one binary.
//
//   bun tests/tools/freestanding.ts --binary build/studio/server
//
// Needs root and chroot (Linux).
import { readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { Supervised, auditBinary, canConnect, check, copyWithMetadata, freePort, request as send, sleep, withTemporary } from "../lib";

const usage = "usage: bun tests/tools/freestanding.ts [-h] --binary BINARY";
let values: { binary?: string; help?: boolean };
try {
  ({ values } = parseArgs({ options: { binary: { type: "string" }, help: { type: "boolean", short: "h" } } }));
} catch (error) { console.error(usage + "\nerror: " + (error as Error).message); process.exit(2); }
if (values.help) { console.log(usage); process.exit(0); }
if (values.binary === undefined) { console.error(usage + "\nerror: the following arguments are required: --binary"); process.exit(2); }
const binary = resolve(values.binary);

/** One GET; a reply with a body must declare a matching Content-Length. */
const request = (port: number, path = "/") => send(port, path, { requireLength: true });

const chroot = Bun.which("chroot");
check(process.geteuid?.() === 0 && chroot, "needs root and chroot");
auditBinary(binary);
await withTemporary("neokit-empty-root-", async root => {
  copyWithMetadata(binary, join(root, "server"));
  const port = await freePort();
  const server = new Supervised(chroot!, [root, "/server", String(port)], { env: { NEO_THREADS: "1" } });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100 && !ready; attempt++) {
      if (!server.running) throw new Error(await server.drained());
      ready = await canConnect(port);
      if (!ready) await sleep(30);
    }
    check(ready, "Empty-root server did not start");
    const home = await request(port);
    check(home.status === 200 && home.text.includes("LIVE SERVER RENDER"), home.status);
    const urls = [...home.body.toString("latin1").matchAll(/(?:src|href)="(\/_web\/[^"]+)"/g)].map(match => match[1]);
    check(urls.length > 0, "no /_web/ assets in the page");
    for (const url of urls) check((await request(port, url)).status === 200, url);
    check((await request(port, "/greet?name=freestanding")).text.includes("Hello, freestanding"));
    check((await request(port, "/api/status")).status === 200);
    const names = readdirSync(root);
    check(names.length === 1 && names[0] === "server", names);
    console.log("PASS Linux empty-root SSR, embedded CSS/JS, greeting and API: executable only, no libc/loader/shared libraries/assets/runtime files");
  } finally {
    await server.stop({ grace: 10000, strict: true });
  }
});
