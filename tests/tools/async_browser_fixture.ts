// Owns only local verification peers and a NeoKit server; terminate to clean up.
//
//   bun tests/tools/async_browser_fixture.ts --pg-port N [--binary build/async-features/server]
//
// Serves the async features example with slow upstreams (database answers after
// 4 s) for browser checks; prints and writes build/async-browser/ports.json, logs
// to build/async-browser/server.log. Ctrl-C or SIGTERM stops everything. For a
// database, run `bun tests/tools/postgres.ts` (it prints the port). The default
// binary is the one `./neokit test --postgres` builds.
import { closeSync, mkdirSync, openSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { DelayedPeer, Interrupted, ROOT, Supervised, freePort, interruption, jsonDumps, sleep } from "../lib";

const usage = "usage: bun tests/tools/async_browser_fixture.ts [-h] [--binary BINARY] --pg-port PG_PORT";
let values: { binary?: string; "pg-port"?: string; help?: boolean };
try {
  ({ values } = parseArgs({ options: { binary: { type: "string" }, "pg-port": { type: "string" }, help: { type: "boolean", short: "h" } } }));
} catch (error) { console.error(usage + "\nerror: " + (error as Error).message); process.exit(2); }
if (values.help) { console.log(usage); process.exit(0); }
const pgPort = Number(values["pg-port"]);
if (values["pg-port"] === undefined || !Number.isInteger(pgPort)) {
  console.error(usage + "\nerror: " + (values["pg-port"] === undefined ? "the following arguments are required: --pg-port" : `argument --pg-port: invalid int value: '${values["pg-port"]}'`));
  process.exit(2);
}
const binary = resolve(values.binary ?? join(ROOT, "build/async-features/server"));

const interrupted = interruption();
const peer = await DelayedPeer.start();
peer.delays = { "/auth": 0.1, "/fast": 1.2, "/slow": 6, "/timeout": 8 };
const port = await freePort();
const directory = join(ROOT, "build/async-browser");
mkdirSync(directory, { recursive: true });
const log = openSync(join(directory, "server.log"), "w");
const server = new Supervised(binary, [String(port)], {
  env: { ...process.env, NEO_THREADS: "1", NEOKIT_ASYNC_HTTP_PORT: String(peer.port), NEOKIT_ASYNC_PG_PORT: String(pgPort), NEOKIT_ASYNC_PG_DELAY: "4" },
  stdout: log, stderr: log,
});
try {
  await server.started;
  const info = { port, http_peer: peer.port, server_pid: server.pid };
  writeFileSync(join(directory, "ports.json"), jsonDumps(info));
  console.log(jsonDumps(info));
  while (server.running) await Promise.race([interrupted, sleep(200)]);
} catch (error) {
  if (!(error instanceof Interrupted)) throw error;
} finally {
  await server.stop({ grace: 4000 });
  closeSync(log);
  await peer.close();
}
process.exit(0);
