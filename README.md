# NeoKit — public alpha 0.1.0-alpha.1

HTML-first applications with native Neo SSR, static export, typed templates and optional browser modules. `neoc` compiles the deployed server; it never starts Python, Bun, Node, a database subprocess or a JS engine. Build tools run on the developer machine. This is an independent framework, with the supported profiles and remaining gaps below.

## Start

The [alpha release guide](docs/alpha-release.md) covers installation, the standalone starter, and HTTPS deployment of Meridian. Maintainers can create the source SDK with `./neokit release --neo-archive neo-0.2.0-stack-fix.tar.gz --neo-sha256 "$NEO_SHA256" --out build/neokit-0.1.0-alpha.1.tar.gz`. Use the Neo archive with the compiler launcher stack fix.

Python 3.11+ runs the framework CLI, Bun bundles browser modules, and native Neo 0.2.0 uses LLVM to build the server. Use the bundled SDK or set `NEO_HOME` to an installed native Neo toolchain when developing from a checkout. Keep `axon-libs` beside the NeoKit checkout. Dependencies are source packages pinned by `neo.lock`.

```sh
bun install --frozen-lockfile
./neokit doctor
./neokit dev
# http://127.0.0.1:8094
./neokit init /path/to/new-app
./neokit build --app /path/to/new-app --out build/new-app
./neokit serve --app /path/to/new-app --out build/new-app --no-build
```

`./web` remains a compatibility alias. Sources use `.neo`, with `neo.toml` as the package manifest and `web.toml` as framework configuration. `NEO_THREADS` selects native workers (one by default). `NEOKIT_LIBRARY_CACHE=off` disables prepared dependency objects; otherwise they are shared under `~/.cache/neokit/native`. Release builds use LLVM O3. Development builds keep application optimization low while retaining type, effect, ownership and runtime checks.

## Showcase applications

Run `python3 examples/run.py` to build and start three local native previews:
[Forma SSR site](examples/ssr-site/README.md), [Fieldnotes blog](examples/blog/README.md),
and [Meridian SaaS analytics](examples/saas/README.md). They include complete pages,
authored illustrations, native SVG charts, search, pagination over 10,000 synthetic
accounts, and measured per-response timing badges. No credentials or services are
needed. [Examples and performance measurements](examples/README.md).

`[server] timing=true` adds an optional `Server-Timing: render` header for buffered
handler/loader/template execution through response encoding. It excludes delivery
and browser work. Empty async placeholders are left untouched; streamed responses
do not report complete deferred-render duration with this option.

## Applications and routing

```text
app/layout.view                   root document
app/page.toml, page.neo, page.view page configuration, loader, template
app/org/layout.view               nested persistent layout
app/org/[org]/items/[id]/          multiple named parameters
app/docs/[...slug]/                required terminal catch-all
app/docs/[[...slug]]/              optional terminal catch-all
app/api/items/route.toml, route.neo HTTP endpoint or typed action
app/hooks.neo                     optional middleware and startup hook
app/not-found.view                @props() custom 404
app/error.view                    @props(status:int,message:text) returned 5xx
app/migrate.neo                   explicit native migration entry
components/*.view                 checked reusable templates
client/index.js                   optional browser entry
styles/*.css, public/*            compiled CSS and embedded assets
```

A page configuration names a loader (`loader="load"`) and chooses `mode="ssr"` or `"static"`, `client=true/false`, `export=true/false`, and explicit dynamic export `paths`. A loader receives `WebRequest` and returns a Neo record matching the template props, including `title:text`. Effectful loaders may read the database. `web_param(request,"org")` reads a matched parameter. Static segments take precedence over parameters and catch-alls across pages and APIs. Invalid encoded traversal and ambiguous route shapes are rejected.

Endpoints return `WebResponse`; `methods=["POST","PATCH","DELETE","OPTIONS"]` explicitly selects methods. GET automatically permits HEAD, and mismatches return 405 with Allow. Helpers provide status codes, safe response headers, relative redirects, URLencoded forms, cookies and multipart parsing. Native request framing accepts Content-Length or chunked bodies, rejects conflicting framing and limits headers to 16 KiB and total input storage to about 16 MiB. Multipart upload handling is buffered and bounded, rather than a direct-to-storage streaming upload.

`[server] before="guard"`, `after="decorate"` names functions imported from `app/hooks.neo`. A before hook returns `web_continue()` or a response. After hooks decorate buffered responses. Streaming handlers own the bytes and headers after the before hook; buffered after/error hooks cannot rewrite an already-started stream.

## Templates and actions

```html
@props(title:text, names:text[], destination:url)
<h1>{{ title }}</h1>
<a href="{{ destination }}">Continue</a>
<ul>{% for name in names %}<li>{{ name }}</li>{% endfor %}</ul>
{% include Badge label=title %}
{% component Card title=title %}<p>Checked content slot.</p>{% endcomponent %}
```

Types are `text`, `int`, `bool`, trusted `html` (`WebHtml`), `url` (`WebURL`) and corresponding scalar arrays other than URL arrays. `web_url("/relative/path")` constructs a checked relative URL; an invalid URL becomes `#`. Text and quoted attributes are escaped. URL-valued attributes require a URL prop. Includes, slots, nested loops and boolean conditions are checked during compilation. A slot component declares `content:html`. Arbitrary expressions, dynamic executable attributes and script/style interpolation are rejected. Trusted HTML remains an explicit application responsibility.

A typed endpoint can declare this `route.toml`:

```toml
handler="save_item"
input_type="SaveItem"
methods=["POST"]
[input]
title={type="text",min=1,max=200}
quantity={type="int",min=0,max=1000}
published="bool"
```

Its Neo handler receives `(request:WebRequest,input:SaveItem)`. Generated code parses JSON or form fields, returns 400 for malformed input and 422 for invalid fields, then calls the checked handler. Bounds count UTF-8 bytes for text. Mutations require the configured `NEOKIT_ORIGIN` by default; deliberate public/token-auth APIs can set `csrf=false`. Application auth guards must additionally validate the signed session and its CSRF token, as the dashboard example does. This is schema-driven action code generation, not arbitrary remote function invocation.

## Authentication and databases

Scaffolded applications can import the public modules directly: `pkg:neokit/auth`, `pkg:neokit/database`, `pkg:neokit/cache`, `pkg:neokit/stream` and `pkg:neokit/oauth`. They resolve through the application lockfile, including transitive library dependencies.

`examples/dashboard` is a functioning task app with login/logout, signed sessions, CSRF checks, per-user reads and CRUD, progressive forms, JSON APIs and restart persistence. Password verification uses the reusable native PBKDF2-HMAC-SHA256 library with a 600,000-round minimum. Session cookies are HttpOnly, SameSite=Lax and Secure for an HTTPS origin. Login attempts share an account-wide file-locked rate limit.

```sh
./neokit password
# Put the resulting hash in your local environment; never put plaintext passwords in arguments.
export NEOKIT_ADMIN_PASSWORD_HASH='p1.600000.…'
export NEOKIT_SESSION_SECRET='a-random-secret-of-at-least-32-bytes'
export NEOKIT_ORIGIN='http://127.0.0.1:8094'
export NEOKIT_DATA_DIR='/path/to/private/app-data'
mkdir -p "$NEOKIT_DATA_DIR"
./neokit migrate --app examples/dashboard
./neokit serve --app examples/dashboard --out build/dashboard
```

`NEOKIT_ADMIN_USER` defaults to `admin`. The embedded document store serializes transactions with a separate flock file, writes a new document, fsyncs it, atomically renames it and fsyncs the directory. It limits documents to 16 MiB; the example caps tasks at 10,000. It is a local single-file JSON store, with no joins, indexes or SQLite compatibility. `app/migrate.neo` returns `IntResult`; migrations and schema versions remain explicit application code.

The reusable PostgreSQL package supports native v3 wire connections over caller-owned TCP/TLS, trust or SCRAM-SHA-256 auth, parameterized extended queries, SQL error recovery and bounded connection-pool bookkeeping. Results preserve text/NULL values rather than coercing decimal or large integers. The caller supplies credentials, verified transport, deadlines and resource cleanup. See [database profile](../axon-libs/postgres/README.md). The reusable SELECT/INSERT/UPDATE/DELETE builder quotes checked identifiers, binds values separately, supports null-safe equality filters and requires explicit mutation predicates. There is no ORM, MD5 auth, COPY or binary-column codec. A standalone independent protocol peer tests authentication, framing, injection-shaped parameters and reuse; The async suite also exercises a real PostgreSQL server using SCRAM.

Optional provider login uses native OAuth2 authorization-code flow with PKCE S256, signed provider-bound state, a browser challenge cookie and authenticated HTTPS userinfo. Configure `NEOKIT_OAUTH_AUTHORIZE_URL`, `NEOKIT_OAUTH_TOKEN_URL`, `NEOKIT_OAUTH_USERINFO_URL`, `NEOKIT_OAUTH_CLIENT_ID`, optional `NEOKIT_OAUTH_CLIENT_SECRET`, `NEOKIT_OAUTH_SCOPE` and `NEOKIT_OAUTH_SUBJECT_CLAIM` (default `sub`). Register the exact `${NEOKIT_ORIGIN}/oauth/callback` URL. Providers must expose a stable subject in userinfo. Set both `NEOKIT_OIDC_ISSUER` and `NEOKIT_OIDC_JWKS_URL` to enable native RS256 ID-token/JWKS validation with exact issuer/audience, expiry, nonce and matching userinfo subject. This requires `openid` scope and the `sub` claim. [The shared library profile](../axon-libs/jwt/README.md) lists supported algorithms and limits. No real provider account was configured during these tests.

## Caching, streaming and assets

Public SSR pages can set `cache_ttl=60`, `stale_ttl=300`, `cache_tag="catalog"`. The shared native file cache publishes entries atomically; keys include build identity, path/query and tag generation. Requests with cookies or Authorization bypass the cache. `web_cache_invalidate_tag` invalidates captured tag generations without publishing a racing stale render under a new generation. HIT, MISS and STALE responses identify cache behavior. Static routes and public caches must not contain private data.

A stale hit returns existing HTML and enqueues native background revalidation. Run `./neokit revalidate --app … --out …` alongside the server, with the same `NEOKIT_CACHE_DIR`, a random `NEOKIT_REVALIDATE_TOKEN` of at least 32 bytes, and `NEOKIT_REVALIDATE_HOST`/`NEOKIT_REVALIDATE_PORT` (127.0.0.1/8094 by default). The queue is bounded, deduplicated briefly and drained by a separate native worker. A crashed worker can lose a drained job; hard expiry still forces a fresh render. Individual cache entries and queue size are bounded; operators must manage total cache-directory disk usage.

Pages and APIs can opt into `async=true`: native database and network waits suspend the loader while its worker serves other requests. A `stream=true` endpoint can send a shell and independent deferred HTML sections, with cancellation, deadlines and bounded cooperative backpressure. `async=true` also allows the stream handler to wait before sending its shell. See [async rendering and streaming](docs/async-rendering.md), [the PostgreSQL/network example](examples/features/app/async/route.neo), and [the raw chunk example](examples/features/app/stream/route.neo). This is an HTML/widget model, without React/RSC compatibility.

`[site]` and `[metadata]` support description, robots, OpenGraph fields and canonical URLs. A configured public site URL generates robots.txt and a sitemap from explicit public export paths. `[images] files=["photo.jpg"], widths=[320,640,1280], formats=["webp"]` generates content-hashed responsive variants at build time using Pillow. Bind a generated URL with `@asset(image:photo.jpg:640:webp)`. Widths above the original are clamped. There is no on-demand image server. Embedded text assets negotiate gzip, send ETags and support conditional 304 responses. `/healthz` provides readiness; `[server] access_log=true` emits JSON request timing/status to stderr and adds request IDs to buffered replies. Stream logs cannot infer a buffered status.

## Browser behavior and development

Realtime endpoints opt into `realtime=true`; native applications publish named
JSON events to bounded channels and browsers subscribe through a small helper
or `context.subscribe` in a widget. Channels share replay history across a
server's workers, with reconnect, heartbeat and cleanup support. The backend is
ephemeral shared memory, with no Redis. Publications wake workers through their
kernel-watched IPC sockets; subscriptions do not poll channel data. A startup
hook can register an independent demo producer using Neo's reusable timers.
[API, limits and SaaS example](docs/realtime.md),
[push delivery measurements](bench/push-results/2026-11-08/REPORT.md).
[Matched production SSE comparison against Next and Astro](bench/realtime-parity-results/2026-11-08/REPORT.md);
[prepared-frame optimization and regression checks](bench/realtime-opt-results/2026-11-08/REPORT.md)
covers 1/100/1,000 subscribers, delivery latency, CPU, memory and a separately
measured serial publish/delivery loop.

Widgets export `mount(element,context)` and may return cleanup. The runtime lazily loads registered widget modules, passes parsed props and shared state/action helpers, and disposes replaced widgets. `signal`, `state` and `computed` in `client/state.js` support subscriptions and stable keyed state. `createAction` tracks pending/errors and supports optimistic rollback.

`data-web-link` enables same-origin navigation, history, scroll and metadata updates. Shared nested layouts and explicitly persistent nodes retain their DOM identity; replaced page widgets clean up. `data-web-prefetch` opts public pages into bounded prefetch; cacheability headers are respected and successful mutations invalidate prefetched HTML. `data-web-form` enhances GET and POST forms with normal browser fallback. JavaScript-free pages remain possible. This is an islands/widgets model, without a general React-style component renderer.

Development watches source/CSS/browser changes, retains the old server on failed builds, exposes a compile-error overlay and refreshes connected pages after a successful build. Browser-only edits use a hot module path with preserved keyed stores; other edits restore nonsecret form drafts across reload. This behavior has DOM-level tests and [actual-browser checks](bench/browser-results/2026-11-07/REPORT.md), including preserved state, cleanup, hot updates, visible compile errors and draft recovery.

Tailwind is optional: add `[css] tailwind=["site.css"]` to `web.toml`, then use `@import "tailwindcss" source(none);` and explicit `@source` paths in `styles/site.css`. The frozen Bun lockfile pins its build tools. Complete utility names are required. Plain CSS apps need no Tailwind dependency.

## Export, verification and deployment

```sh
./neokit export --app examples/studio --out build/static --dest build/export
./neokit preview --dir build/export
./neokit test
python3 tests/dev_css_smoke.py
```

Export renders eligible pages with `server=false`, empty query/body and timestamp zero. `export=false` and API routes are omitted; `--strict` rejects omissions. Exports contain ordinary HTML/CSS/JS. Use `export=false` for authenticated pages. Custom 5xx templates handle returned error responses, not arbitrary runtime traps or nested exception boundaries.

The scratch-container recipe embeds assets and includes native healthcheck/migration binaries and a CA bundle for outbound HTTPS. The Linux binary is intended to have no libc, JS engine or shared-library imports; The dashboard passes the real integration in ARM64 Linux scratch and x86-64 Linux scratch under emulation, with nonroot UID, read-only root, dropped capabilities and Docker init. Both ELF builds have no interpreter, dynamic dependencies or undefined symbols. The full production build pipeline and public deployment remain separate checks. macOS native binaries are checked for undefined symbols. TLS ingress uses Caddy. The native server stops accepting on SIGTERM, drains active responses to its configured grace deadline and cancels outstanding async work.

```sh
docker build --build-context neo="$NEO_HOME" --build-context neo-libs=../axon-libs -t neokit .
# For the dashboard example, create deploy/.env from .env.example, then:
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --entrypoint /migrate app
docker compose --env-file deploy/.env -f deploy/compose.yaml up --build -d
```

The checked-in Compose example runs the dashboard, persistent volumes, a read-only app container with init for PID 1 signal forwarding, native readiness probes and a TLS proxy. A stale-cache application can add `-f deploy/revalidation.compose.yaml`, set `NEOKIT_APP` and `NEOKIT_REVALIDATE_TOKEN`, and run the packaged native worker sharing its cache volume. No cloud-specific adapters, edge-runtime distribution, distributed cache coordination, deployment rollbacks or hosted control plane are implemented.

## Reusable foundations and performance

Common functionality was added below NeoKit first: Neo standard-library HTTP server parsing/headers/forms/cookies/routing, shared transport types, chunked input, typed action input, prepared HMAC/PBKDF2 and request logging. `axon-libs` owns independently reusable web-security, web-cache, document-store, postgres, jwt and oauth2 packages. NeoKit composes these libraries rather than implementing private copies.

[Current production benchmark](bench/parity-results/2026-11-07-windowed-oidc/REPORT.md) compares the saved pre-expansion NeoKit binary, the expanded framework and an installed production Next.js build. It includes uncached rendering plus an authenticated database-backed task page and durable mutations, independent semantic validation, shuffled repeated trials and raw results. Reproduce with `python3 bench/parity_performance.py --prepare-next --next-modules /path/to/node_modules` after building `build/catalog/server` and `build/dashboard/server`. Measurements identify the actual installed versions and monolithic build profile; they are not universal framework claims. [Current authenticated prepared-library checks](bench/parity-results/2026-11-07-windowed-oidc/CACHE-PROFILE.md) and [the broader earlier profile](bench/parity-results/2026-11-07/CACHE-PROFILE.md) cover the normal cache-enabled path separately.

Historical results remain available: [framework catalog](bench/frameworks/results/2026-10-26/REPORT.md), [Tailwind](bench/tailwind-framework-results/2026-10-26/REPORT.md), [build speed](bench/build-speed-results/2026-10-26/REPORT.md) and [interactive list checks](bench/list-stress-results/2026-10-26/REPORT.md). Their older source snapshots and limitations still apply.

Large-list rendering uses a [shared windowed renderer and inert native page data](docs/windowed-lists.md). The 10,000-record browser fixture keeps a bounded DOM, preserves focused drafts, and includes an explicit full-DOM diagnostic. [Browser measurements and checks](bench/browser-results/2026-11-07-windowed/REPORT.md) cover native SSR and static export.

[NeoKit / Next / Astro core stats after the prepared-library fix](bench/core-results/2026-11-07-prepared-fixed/REPORT.md) record matched uncached SSR throughput, latency, sampled process-group RSS, CPU time per request, equal-rate CPU use, startup and payload size. The primary NeoKit measurements use prepared libraries.

The [prepared-library fix measurements](bench/core-results/2026-11-07-prepared-fixed/PREPARED.md) compare a saved prepared binary with the fixed prepared compiler and the current monolithic build. Reusable changes are in Neo: checked return-alias contracts, pre-optimization dependency IR, and consistent small-helper/constant-argument inlining. [The earlier core stats](bench/core-results/2026-11-07/REPORT.md) remain available.

[Initial async rendering verification and performance](bench/async-results/2026-11-08/REPORT.md) covers real PostgreSQL/network waits, progressive browser sections, cancellation, backpressure and shutdown on macOS/ARM64 Linux. The [updated 63-trial core comparison](bench/core-results/2026-11-08-async/REPORT.md) measures production prepared NeoKit against Next and Astro; separate tests cover mixed async loads and prepared-versus-monolithic authentication. The latter builds are within 0.9% of each other in this run, with an 8.2% decrease versus the saved pre-async prepared executable on that file-backed workload. See the reports for exact scope, raw samples and limits.

The [authenticated-rendering follow-up](bench/core-results/2026-11-08-auth-fixed/FIX.md) puts the performance fixes in Neo: caller-local inline constraints, pre-budget library hints, separate application budgets and an exact-buffer escaped JSON decoder. [Current measurements and regression checks](bench/core-results/2026-11-08-auth-fixed/AUTH-FIX.md) compare saved pre-async/regressed binaries with fixed prepared and monolithic builds, rerun Next/Astro SSR, and verify async PostgreSQL/network streaming.
