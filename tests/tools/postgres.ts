// A private, disposable PostgreSQL cluster for the PostgreSQL suites (tests/postgres)
// and the async browser fixture:
//
//   bun tests/tools/postgres.ts [--port N] [-- command [args...]]
//
// It runs initdb/pg_ctl under a new temporary directory, on a free 127.0.0.1
// port (or N), with SCRAM authentication for user "test", password "pencil" and
// database "test" (what examples/features/app/async connects with). With a
// command it exports NEOKIT_ASYNC_PG_PORT and the libpq PG* variables to that
// command, then stops and deletes the cluster and exits with the command's status:
//
//   bun tests/tools/postgres.ts -- bun test --timeout 600000 ./tests/postgres
//
// (`./neokit test --postgres` runs exactly that.) Without a command it prints the
// connection as JSON and keeps the cluster until Ctrl-C, for example for
// `bun tests/tools/async_browser_fixture.ts --pg-port N`.
// The PostgreSQL server binaries come from NEOKIT_PG_BIN, else the first
// directory on PATH with initdb, else the newest /usr/lib/postgresql/*/bin. The
// system's own PostgreSQL service is never used or started. As root, the cluster
// runs as the "postgres" account (initdb refuses root).
import { spawn, spawnSync } from "node:child_process";
import { appendFileSync, chownSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { constants, tmpdir } from "node:os";
import { join } from "node:path";
import { check, freePort, run } from "../lib";

export type Postgres = { port: number; user: string; password: string; database: string; directory: string };

/** The directory holding initdb, pg_ctl, createdb and psql. */
export function postgresBin(): string {
  const configured = process.env.NEOKIT_PG_BIN;
  if (configured) return configured;
  for (const directory of (process.env.PATH ?? "").split(":")) if (directory && existsSync(join(directory, "initdb"))) return directory;
  for (const base of ["/usr/lib/postgresql", "/usr/local/lib/postgresql"]) {
    if (!existsSync(base)) continue;
    const versions = readdirSync(base).filter(name => existsSync(join(base, name, "bin/initdb"))).sort((a, b) => Number(b) - Number(a));
    if (versions.length) return join(base, versions[0], "bin");
  }
  throw new Error("No PostgreSQL server binaries (initdb, pg_ctl): install PostgreSQL or set NEOKIT_PG_BIN");
}

/** A disposable PostgreSQL cluster for `body`: initdb under a new temporary
 *  directory, SCRAM authentication only, listening on 127.0.0.1 at a free port;
 *  stopped and deleted afterwards. */
export async function privatePostgres<T>(body: (cluster: Postgres) => Promise<T>, options: { port?: number } = {}): Promise<T> {
  const bin = postgresBin();
  const directory = mkdtempSync(join(tmpdir(), "neokit-pg-"));
  const cluster: Postgres = { port: options.port ?? await freePort(), user: "test", password: "pencil", database: "test", directory };
  const asRoot = process.getuid?.() === 0;
  const owner = asRoot ? [Number(run(["id", "-u", "postgres"]).trim()), Number(run(["id", "-g", "postgres"]).trim())] as const : undefined;
  const server = (command: string[]) => (owner ? ["runuser", "-u", "postgres", "--", ...command] : command);
  const client = { PGHOST: "127.0.0.1", PGPORT: String(cluster.port), PGUSER: cluster.user, PGPASSWORD: cluster.password, PGDATABASE: cluster.database };
  const execute = (command: string[], env: Record<string, string> = {}, allowFailure = false) => {
    const result = spawnSync(command[0], command.slice(1), { cwd: directory, env: { ...process.env, ...env }, encoding: "utf8" });
    if (!allowFailure && (result.error || result.status !== 0)) throw new Error(`${command.join(" ")} failed (${result.status}):\n${result.error ?? ""}${result.stdout}${result.stderr}`);
    return result.stdout ?? "";
  };
  const data = join(directory, "data"), log = join(directory, "postgres.log"), secret = join(directory, "password");
  let started = false;
  try {
    if (owner) chownSync(directory, owner[0], owner[1]);
    writeFileSync(secret, cluster.password + "\n", { mode: 0o600 });
    if (owner) chownSync(secret, owner[0], owner[1]);
    execute(server([join(bin, "initdb"), "-D", data, "-U", cluster.user, "--pwfile", secret, "--auth", "scram-sha-256", "--encoding", "UTF8", "--locale", "C"]));
    appendFileSync(join(data, "postgresql.conf"), `\n# NeoKit private test cluster\nlisten_addresses = '127.0.0.1'\nport = ${cluster.port}\n` +
      `unix_socket_directories = '${directory}'\npassword_encryption = 'scram-sha-256'\nfsync = off\n`);
    execute(server([join(bin, "pg_ctl"), "-D", data, "-l", log, "-w", "-t", "60", "start"]));
    started = true;
    execute([join(bin, "createdb"), cluster.database], client);
    const scram = execute([join(bin, "psql"), "-X", "-tA", "-c",
      "select (select rolpassword like 'SCRAM-SHA-256$%' from pg_authid where rolname=current_user) and (select bool_and(auth_method='scram-sha-256') from pg_hba_file_rules)"], client).trim();
    check(scram === "t", "the private cluster does not use SCRAM only: " + scram);
    return await body(cluster);
  } catch (error) {
    if (existsSync(log)) console.error("PostgreSQL log:\n" + readFileSync(log, "utf8"));
    throw error;
  } finally {
    if (started) {
      const stop = (mode: string) => execute(server([join(bin, "pg_ctl"), "-D", data, "-m", mode, "-w", "-t", "30", "stop"]), {}, true);
      stop("fast");
      if (existsSync(join(data, "postmaster.pid"))) stop("immediate");
    }
    rmSync(directory, { recursive: true, force: true });
  }
}

async function main(argv: string[]) {
  const usage = "usage: bun tests/tools/postgres.ts [--port N] [-- command [args...]]";
  let port: number | undefined;
  let i = 0;
  for (; i < argv.length; i++) {
    if (argv[i] === "--") { i++; break; }
    // Bun itself consumes a "--" right after the script path, so the command may start here.
    if (!argv[i].startsWith("-")) break;
    const [flag, inline] = argv[i].split("=", 2);
    if (flag === "-h" || flag === "--help") { console.log(usage); process.exit(0); }
    if (flag !== "--port") { console.error(usage); process.exit(2); }
    port = Number(inline ?? argv[++i]);
    if (!Number.isInteger(port) || port < 1 || port > 65535) { console.error(usage); process.exit(2); }
  }
  const command = argv.slice(i);
  let status = 0;
  await privatePostgres(async cluster => {
    const env = { NEOKIT_ASYNC_PG_PORT: String(cluster.port), PGHOST: "127.0.0.1", PGPORT: String(cluster.port), PGUSER: cluster.user, PGPASSWORD: cluster.password, PGDATABASE: cluster.database };
    if (command.length) {
      const child = spawn(command[0], command.slice(1), { stdio: "inherit", env: { ...process.env, ...env } });
      // Ctrl-C reaches the command as well; stop the cluster once it has exited.
      const forward = (signal: NodeJS.Signals) => () => { try { child.kill(signal); } catch {} };
      process.on("SIGINT", () => {});
      process.on("SIGTERM", forward("SIGTERM"));
      status = await new Promise<number>(done => {
        child.once("error", error => { console.error(error.message); done(127); });
        child.once("exit", (code, signal) => done(code ?? 128 + (signal ? constants.signals[signal] : 0)));
      });
    } else {
      console.log(JSON.stringify({ ...cluster, env }));
      console.error(`PostgreSQL ${cluster.user}@127.0.0.1:${cluster.port}/${cluster.database} (SCRAM); Ctrl-C stops and deletes it.`);
      await new Promise(done => { process.once("SIGINT", done); process.once("SIGTERM", done); });
    }
  }, { port });
  process.exit(status);
}

if (import.meta.main) await main(process.argv.slice(2));
