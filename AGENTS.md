# NeoKit

This is the separate experimental web framework project, named NeoKit. Keep application/framework code outside the language compiler.

- `./neokit build`, `./neokit serve`, `./neokit export`, `./neokit test` drive the example.
- `NEO_HOME` selects the external `neoc` checkout; auto-detect `../neo`.
- Native runtime code stays Neo and C-free. Bun and LLVM are build tools;
  JavaScript executes in the browser. No JS engine runs inside the server.
- The `neokit` CLI is native Neo in `cli/` (see `cli/README.md`), built on
  first use by the `neokit` sh launcher. Use only `std:` imports there.
- Use Bun for JavaScript tooling and tests. Integration tests (`tests/core`,
  `tests/extended`, `tests/postgres`) drive only the `neokit` CLI; shared
  helpers belong in `tests/lib.ts`.
- Keep template interpolation escaped; never accept raw user HTML or executable
  template expressions. Browser behavior belongs in external module files.
- Test SSR and exported pages in an actual browser as well as HTTP tests.
- State the supported subset explicitly; this is not Next.js compatibility.
