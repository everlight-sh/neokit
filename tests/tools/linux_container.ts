// Run the dashboard integration in a nonroot scratch Linux image.
//
// Build build/linux/dashboard/server and migrate first. Requires a running
// Docker daemon of that architecture. No package installation occurs.
//
//   bun tests/tools/linux_container.ts [--out build/linux/dashboard] [--platform linux/arm64|linux/amd64]
//
// The integration is tests/core/dashboard.test.ts, run against a wrapper that
// starts the server image with `docker run` (the test takes its binary from
// NEOKIT_DASHBOARD_BINARY).
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { ROOT, check, copyWithMetadata, jsonDumps, run } from "../lib";

/** Quotes text for a POSIX shell: safe words as they are, anything else in single quotes. */
function shellQuote(text: string) {
  if (!text) return "''";
  return /^[\w@%+=:,./-]+$/.test(text) ? text : "'" + text.replace(/'/g, `'"'"'`) + "'";
}

const usage = "usage: bun tests/tools/linux_container.ts [-h] [--out OUT] [--platform {linux/arm64,linux/amd64}]";
const PLATFORMS = ["linux/arm64", "linux/amd64"];
let values: { out?: string; platform?: string; help?: boolean };
try {
  ({ values } = parseArgs({ options: { out: { type: "string" }, platform: { type: "string" }, help: { type: "boolean", short: "h" } } }));
} catch (error) { console.error(usage + "\nerror: " + (error as Error).message); process.exit(2); }
if (values.help) { console.log(usage); process.exit(0); }
if (values.platform !== undefined && !PLATFORMS.includes(values.platform)) {
  console.error(usage + `\nerror: argument --platform: invalid choice: '${values.platform}' (choose from 'linux/arm64', 'linux/amd64')`);
  process.exit(2);
}
const DASHBOARD_TEST = "tests/core/dashboard.test.ts";
const out = resolve(values.out ?? join(ROOT, "build/linux/dashboard"));

/** The dashboard integration against `binary`; data directories go under `temporary`. */
function dashboard(binary: string, temporary: string) {
  check(readFileSync(join(ROOT, DASHBOARD_TEST), "utf8").includes("NEOKIT_DASHBOARD_BINARY"), `${DASHBOARD_TEST} does not honor NEOKIT_DASHBOARD_BINARY, so it would test a host binary`);
  const result = spawnSync(process.execPath, ["test", "--timeout", "600000", "./" + DASHBOARD_TEST], {
    cwd: ROOT, stdio: "inherit", env: { ...process.env, NEOKIT_DASHBOARD_BINARY: binary, TMPDIR: temporary },
  });
  if (result.error) throw result.error;
  check(result.status === 0, `dashboard integration failed (${result.status})`);
}

const server = readFileSync(join(out, "server"));
check(server.subarray(0, 4).equals(Buffer.from("\x7fELF", "latin1")), "Expected a Linux ELF executable");
const work = join(ROOT, "build/linux/tmp");
mkdirSync(work, { recursive: true });
const tag = "neokit-verify-" + randomUUID().replace(/-/g, "").slice(0, 12);
const platformFlags = values.platform ? ["--platform", values.platform] : [];
const identity: Record<string, unknown> = {
  image_tag: tag, docker_host_architecture: run(["docker", "info", "--format", "{{.Architecture}}"]).trim(), platform: values.platform ?? null,
  binary_sha256: createHash("sha256").update(server).digest("hex"),
  profile: "scratch, Docker init, nonroot host UID, read-only root, all capabilities dropped, no-new-privileges; host-mounted disposable data",
  checks: "600000-round password login; CSRF and user authorization; 24 concurrent writes, two workers, restart persistence",
};
try {
  const context = mkdtempSync(join(work, "image-"));
  try {
    for (const name of ["server", "migrate"]) copyWithMetadata(join(out, name), join(context, name));
    writeFileSync(join(context, "Dockerfile"), 'FROM scratch\nCOPY server /server\nCOPY migrate /migrate\nUSER 65532:65532\nENTRYPOINT ["/server"]\n');
    const built = spawnSync("docker", ["build", ...platformFlags, "-q", "-t", tag, context], { stdio: "inherit" });
    if (built.error) throw built.error;
    check(built.status === 0, `docker build failed (${built.status})`);
    identity.image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).trim();
    const runner = join(context, "runner");
    writeFileSync(runner, '#!/bin/sh\nset -eu\nport=$1\nexec docker run --rm --init --read-only --cap-drop=ALL --security-opt=no-new-privileges ' +
      '--user "$(id -u):$(id -g)" -p "127.0.0.1:$port:$port" -v "$NEOKIT_DATA_DIR:$NEOKIT_DATA_DIR" ' +
      "-e NEO_THREADS -e NEOKIT_DATA_DIR -e NEOKIT_ORIGIN -e NEOKIT_SESSION_SECRET -e NEOKIT_ADMIN_PASSWORD_HASH " + platformFlags.map(shellQuote).join(" ") + " " + shellQuote(tag) + ' "$port"\n');
    chmodSync(runner, 0o755);
    dashboard(runner, work);
  } finally {
    rmSync(context, { recursive: true, force: true });
  }
  identity.passed = true;
  writeFileSync(join(out, "verification.json"), jsonDumps(identity, { indent: 2 }) + "\n");
  console.log("PASS Linux scratch integration; no shell, libc or dynamic loader in application image");
} finally {
  spawnSync("docker", ["image", "rm", tag], { stdio: ["inherit", "ignore", "inherit"] });
}
