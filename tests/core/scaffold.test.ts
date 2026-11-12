// Initialize and compile a locked consumer outside its generated output tree.
import { test, expect } from "bun:test";
import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { neokit, request, running, withTemporary } from "../lib";

const replaceAll = (text: string, search: string, replacement: string) => text.split(search).join(replacement);

test("scaffold", async () => {
  await withTemporary("neokit-init-", async directory => {
    const app = join(directory, "new-app");
    const created = neokit(["init", app]);
    expect(created.stdout).toContain("Created " + realpathSync(app));
    expect(existsSync(join(app, "neo.lock")) && statSync(join(app, "neo.lock")).isFile()).toBe(true);
    const source = join(app, "app/page.neo");
    const original = readFileSync(source, "utf8");
    let updated = replaceAll(original, 'import "pkg:neokit"', 'import "pkg:neokit"\nimport "pkg:neokit/auth"\nimport "pkg:neokit/database"');
    updated = replaceAll(updated, "=PageProps {", '=let query=pg_select("tasks",arr(i in 0..1) "id",arr(i in 0..0) pg_field("",pg_null()),10) in check query.ok in let session=web_session_verify("","",1) in check !session.authenticated in PageProps {');
    expect(updated).toContain('import "pkg:neokit/database"');
    expect(updated).toContain("check !session.authenticated in PageProps {");
    writeFileSync(source, updated);
    // Updating a package's own application source is allowed by its lock;
    // only locked dependencies must remain unchanged.
    const out = join(directory, "separate-output");
    neokit(["build", "--app", app, "--out", out]);
    await running(join(out, "server"), {}, async ({ port }) => {
      const reply = await request(port, "/", { requireLength: true });
      expect(reply.status).toBe(200);
      expect(reply.text).toContain("Hello from NeoKit");
    });
  });
});
