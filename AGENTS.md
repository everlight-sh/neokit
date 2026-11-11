# NeoKit

This is the separate experimental web framework project, named NeoKit. Keep application/framework code outside the language compiler.

- `./neokit build`, `./neokit serve`, `./neokit export`, `./neokit test` drive the example.
- `NEO_HOME` selects the external `neoc` checkout; auto-detect `../neo` or `../axon`.
- Native runtime code stays Neo and C-free. Bun/Python/LLVM are build tools;
  JavaScript executes in the browser. No JS engine runs inside the server.
- Use Bun for JavaScript tooling and tests.
- Keep template interpolation escaped; never accept raw user HTML or executable
  template expressions. Browser behavior belongs in external module files.
- Test SSR and exported pages in an actual browser as well as HTTP tests.
- State the supported subset explicitly; this is not Next.js compatibility.
