# Axon Web — experimental v0.1

A separate HTML-first web framework with native Axon SSR, static export, and
optional browser JavaScript. **Axon Web is a working name.** The compiler lives
outside this project (`AXON_HOME`, default `../axon`). Use the updated compiler
checkout with checked native-library caching.

The included **Axon Web starter** uses Tailwind's default palette, system fonts,
spacing and responsive utilities. It demonstrates a real server render, a dynamic
route, a prerendered zero-JS page, a local counter/filter, enhanced GET forms,
and client navigation. The server renders complete HTML before JavaScript runs.

## Try it

Python 3.11+, Bun, and Axon's Python/LLVM toolchain are build dependencies.
The starter uses pinned Tailwind build dependencies; install them once.

```sh
bun install --frozen-lockfile
./web build
./web serve --no-build --port 8094
# Open http://127.0.0.1:8094
```

The deployed server is `build/studio/server`. CSS, public files, and split
browser modules are embedded in that binary. **Python and Bun are never called
by the deployed server.** Linux output is a static executable with no C runtime,
libc, ELF interpreter, shared libraries or JS engine. macOS imports no functions,
but its OS loader still requires libSystem/dyld.

```sh
./web export
./web preview --port 8095
# Open http://127.0.0.1:8095
```

`build/export/` is ordinary HTML/CSS/JS for static hosting. Local widgets and
navigation work there. The greeting form is deliberately replaced with static
content. `/greet` and `/api/status` are explicitly omitted and listed in the
export manifest; `./web export --strict` rejects those omissions. SSR routes
with `export=true` produce default-input snapshots during export.
The renderer receives `server=false`, empty query/body, and timestamp zero.

## Project layout

```text
src/lib.ax             typed request/response, URL decoding, HTTP helpers
compiler/              build-time template compiler, file routes, export, CLI
client/                first-party browser navigation/forms/widget lifecycle
examples/studio/       example application
tests/                 compiler, HTTP, concurrency, export and empty-root checks
build/                 generated source, executables, assets and exports
```

An app has `web.toml`, `app/layout.view`, `app/**/page.toml`, `page.ax` and
`page.view`, optional `components/*.view`, `styles/*.css`, `public/` files,
and `client/index.js`. Use `--app /path/to/app --out /path/to/build` to build
another application. Explicit Axon imports select the framework library;
the example uses relative imports and client modules can import `client/runtime.js`.

`page.toml` names the Axon loader and selects `mode="ssr"` or `mode="static"`,
`client=true/false`, `export=true/false`, and optional dynamic `paths`.
The loader receives `WebRequest` and returns a typed record matching template
props, including `title`. Axon checks every generated prop call. Static server
routes reuse their build HTML; SSR routes run their loaders for each request.
`app/**/route.toml` plus `route.ax` adds a fixed-path API handler returning
`WebResponse`. The current HTTP application profile is GET and HEAD.

## Templates

```html
@props(title: text, count: int, server: bool)
<h1>{{ title }}</h1>
<p>{{ count }}</p>
{% if server %}<p>Rendered on request.</p>{% else %}<p>Exported HTML.</p>{% endif %}
```

Supported props: `text`, `int`, `bool`, and trusted `html` (`WebHtml`). Text and
quoted text attributes are escaped. Components use `{% include Header %}` or
`{% include Badge label=title %}`. Conditions accept bool props or `!prop`.
`@asset(site.css)` / `@asset(client.js)` resolve hashed URLs during the build.
One shared root layout receives `title`, `content`, `client`, and `mode`.

The compiler rejects unknown props, incompatible component arguments, cycles,
arbitrary expressions, dynamic tag/attribute names, event attributes, raw inline
scripts, URL/CSS interpolation, and script/style/comment interpolation. HTML
props are for trusted compiled components or explicitly authored Axon HTML,
never untrusted strings. Loops/data transformations currently live in Axon
loaders; there is no template expression interpreter at runtime.

## Tailwind CSS

Tailwind is optional per application. Enable selected inputs in `web.toml`:

```toml
[css]
tailwind = ["site.css"]
```

Write `styles/site.css` with explicit application source paths:

```css
@import "tailwindcss" source(none);
@source "../app";
@source "../components";
@source "../client";
```

Use complete utility names in `.view` templates, for example
`class="grid grid-cols-1 gap-4 md:grid-cols-2"`. Dynamically concatenated utility
names are unsupported; list complete variants in source or use Tailwind's
`@source inline()` directive. Imported CSS and theme directives work through
the official Tailwind CLI. `./web build`, `serve`, `dev` and `export` compile and
minify enabled inputs before hashing and embedding the final CSS. `./web dev`
rebuilds when templates, components, browser code or application CSS change;
reload the browser after a successful rebuild. No generated CSS is written
back into the source tree.

The included example uses Tailwind 4.3.3 from this project's frozen Bun lockfile.
An external application can install its own packages with
`bun add --dev --exact tailwindcss @tailwindcss/cli`; its installed CLI takes
precedence. Tailwind imports must resolve from that application's stylesheet.
Apps without `[css] tailwind` continue to copy ordinary CSS and need no Tailwind
packages. The native server never invokes Tailwind, Bun or Node; SSR and static
exports serve ordinary CSS without an additional browser runtime.

[Tailwind before/after measurements](bench/tailwind-results/2026-10-26/REPORT.md)
record the earlier Fieldnotes example's clean/warm builds, CSS/JS size and SSR
load results, before the default Tailwind starter redesign.

## Browser behavior

The application imports `start` from `client/runtime.js` and registers lazy
widget modules, each exporting `mount(element)` and optionally returning cleanup.
`data-web-widget="counter"` activates only its matching module. Components are
cleaned up when replaced and are not initialized twice.

`data-web-link` opts into same-origin client navigation with back/forward,
document titles, focus, and native navigation fallback. `data-web-form` plus
`data-web-target="#id"` enhances a normal GET form by requesting escaped HTML
from Axon with `X-Web-Fragment: 1`. Outdated requests are aborted. Plain links
and forms still work when JavaScript is disabled. The initial about page
contains no script tag. Bun splits/minifies browser assets at build time.

### Large interactive list test

Open `/stress` on the running server (normally
`http://127.0.0.1:8094/stress`). Axon renders 1,000 interactive rows initially;
`?n=10000` exercises 10,000 real DOM rows without virtualization and is very heavy.
The page provides live generated-array updates, per-row notes/counters/selection,
filtering, reordering, adding/removing rows and fresh Axon HTML refreshes.

`client/keyed.js` preserves DOM nodes by key. The example updates changed fields
without resetting input values or caret positions. Existing SSR nodes are adopted
on mount. A deliberately naive full-redraw mode demonstrates row replacement and
focus loss. This keyed path is explicit widget behavior; whole-page navigation
and ordinary HTML form-fragment replacement retain their existing semantics.

**Run verification** tests actual node identity, focused input/caret, drafts,
selection, reordering, filtering, add/remove, a fresh server render, and a full-redraw
negative control. Static export runs the same client checks without the server
refresh. The page displays DOM patch timings and sampled frame/focus/layout signals.
These are diagnostics, not proof against every visual artifact or a browser benchmark:
paint/layout, GC, host load and browser-tool instrumentation can cause long frames.
In particular, unvirtualized 10,000-row operations can stall even when state is retained.

[Recorded list-rendering checks](bench/list-stress-results/2026-10-26/REPORT.md)
include the actual-browser results, heavy-case limitations and source snapshots.

## Build speed and profiles

Prepare the compiler/framework libraries once with `./web prepare`. The first
build also prepares them automatically. Clean project builds then reuse only
checked dependency objects; application code is rechecked and compiled.

`./web build`, `serve` and `export` use the production `release` profile
(LLVM `-O3`). `./web dev` defaults to the faster `dev` profile (application `-O0`,
prepared libraries `-O3`);
use `--profile release` to test production optimization while watching files.
Both profiles retain type, refinement, ownership/effect, bounds and overflow checks.
Dependency source/compiler changes invalidate their checked artifacts.
`AXON_LIBRARY_CACHE` selects the build-time cache directory; `off` selects the
monolithic compiler path. The Linux executable remains standalone.

Server builds prerender only routes marked `mode="static"`. Export compiles
only the snapshot renderer. The Python `build()` API keeps `target="all"` as
its compatibility default; `target="server"` or `"static"` selects one output.
A static-only manifest has `server_bytes: null` and records its build target
and profile. A server build's `exported_paths` lists the snapshots actually
produced; `available_export_paths` lists all eligible export paths.

[Build speed comparison and request-performance checks](bench/build-speed-results/2026-10-26/REPORT.md)
include reproducible cold/warm measurements against the recorded original.
The original Astro/Next report below describes the earlier build pipeline.

## Verify and deploy

```sh
./web test
python3 tests/dev_css_smoke.py
python3 bench/measure_ssr.py --requests 500
docker build --build-context axon=../axon --target test-tools -t axon-web-tests .
docker run --rm axon-web-tests
docker build --build-context axon=../axon -t axon-web .
docker run --rm --read-only --cap-drop ALL -p 127.0.0.1:8094:8094 axon-web
```

`./web dev` watches source files, rebuilds, and restarts the server while keeping
the old process alive if compilation fails. Reload the browser after an edit.

This is a tested prototype, not Next.js feature parity. It has one root layout,
one terminal dynamic route parameter, complete buffered SSR responses, explicit
export paths, GET forms, and public API examples. POST mutations/auth sessions,
streaming SSR/Suspense, nested layouts, general template loops, HMR, image
optimization, cache revalidation, and adapters for deployment platforms remain.
A reproducible production benchmark against Astro and Next.js lives in
[bench/frameworks](bench/frameworks/README.md), covering SSR, client widgets,
static export, throughput, latency, CPU, memory, builds and output size.
[Recorded results](bench/frameworks/results/2026-10-26/REPORT.md) apply to the
matched catalog workload; they do not establish framework feature parity.

[Tailwind comparison against Astro and Next.js](bench/tailwind-framework-results/2026-10-26/REPORT.md)
adds production Tailwind integrations, full browser-loaded asset sizes, static
exports, and exploratory browser load/paint measurements.
