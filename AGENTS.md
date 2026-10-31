# Axon Web

This is the separate experimental web framework project, with the working name
Axon Web. Keep application/framework code outside the language compiler.

- `./web build`, `./web serve`, `./web export`, `./web test` drive the example.
- `AXON_HOME` selects the external compiler checkout, default `../axon`.
- Native runtime code stays Axon and C-free. Bun/Python/LLVM are build tools;
  JavaScript executes in the browser. No JS engine runs inside the server.
- Use Bun for JavaScript tooling and tests.
- Keep template interpolation escaped; never accept raw user HTML or executable
  template expressions. Browser behavior belongs in external module files.
- Test SSR and exported pages in an actual browser as well as HTTP tests.
- State the supported subset explicitly; this is not Next.js compatibility.
