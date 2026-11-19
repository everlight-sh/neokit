// `neokit serve` runs the server as a child in neokit's process group. SIGINT
// or SIGTERM sent to neokit alone reaches the server as the same signal (Neo
// 0.2.8's process_signal sends SIGINT), and neokit exits with the server's
// status. The server here is a sh stub at OUT/server (`serve --no-build`) that
// reports the signal it got and exits 0 after SIGINT, 3 after SIGTERM.
import { test, expect } from "bun:test";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, Supervised, check, freePort, sleep, withTemporary } from "../lib";

const STUB = `#!/bin/sh
trap 'echo "server got INT"; exit 0' INT
trap 'echo "server got TERM"; exit 3' TERM
echo "server on $1"
while :; do sleep 0.02; done
`;

for (const [signal, name, status] of [["SIGINT", "INT", 0], ["SIGTERM", "TERM", 3]] as const) {
  test(`serve passes ${signal} sent to neokit on to the server as ${signal}`, () => withTemporary("neokit-serve-", async directory => {
    const out = join(directory, "out");
    mkdirSync(out);
    writeFileSync(join(out, "server"), STUB);
    chmodSync(join(out, "server"), 0o755);
    const port = await freePort();
    const serve = new Supervised(join(ROOT, "neokit"), ["serve", "--no-build", "--app", directory, "--out", out, "--port", String(port)],
      { cwd: directory, stdout: "pipe" });
    try {
      const deadline = performance.now() + 120000;  // the first run may build the CLI
      while (!serve.output.includes(`server on ${port}\n`)) {
        check(serve.running && performance.now() < deadline, serve.output);
        await sleep(10);
      }
      await serve.stop({ signal, group: false, grace: 5000, strict: true });
      const output = await serve.drained();
      check(output.includes(`server got ${name}\n`) && !output.includes(`server got ${name === "INT" ? "TERM" : "INT"}`), output);
      expect(serve.child.exitCode).toBe(status);
    } finally {
      if (serve.running) await serve.stop({ grace: 2000 });
    }
  }), 180000);
}
