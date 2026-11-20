# NeoKit — public alpha 0.1.0-alpha.1

HTML-first applications with native Neo SSR, static export, typed templates and optional browser modules. `neoc` compiles the deployed server; it never starts Bun, Node, a database subprocess or a JS engine. Build tools run on the developer machine. This is an independent framework, with the supported profiles and remaining gaps below.

## Start

The [alpha release guide](docs/alpha-release.md) covers installation, the standalone starter, and HTTPS deployment of Meridian. Maintainers can create the source SDK with `./neokit release --neo-archive neo-0.2.12.tar.gz --neo-sha256 "$NEO_SHA256" --out build/neokit-0.1.0-alpha.1.tar.gz`, using the multi-platform Neo 0.2.12 release archive.

The `neokit` command is a small POSIX sh launcher for NeoKit's native CLI, which is written in Neo (`cli/`). Its first run builds the CLI with Neo and caches the executable under `${XDG_CACHE_HOME:-~/.cache}/neokit/cli`, keyed by the CLI sources and the toolchain, so later runs start it directly. NeoKit needs native Neo 0.2.12 or newer, Bun and Clang/LLVM: Bun bundles browser modules, and Neo uses LLVM to build the CLI and the server. Image variants also need libvips (`vips` and `vipsheader`). Neo compiles its runtime once and caches it under `~/.cache/neo`, so only the first build on a machine pays for it. Use the bundled SDK or set `NEO_HOME` to an installed native Neo toolchain when developing from a checkout. Keep `neo-libs` beside the NeoKit checkout. Dependencies are source packages pinned by `neo.lock`.

```sh
bun install --frozen-lockfile
./neokit doctor
./neokit dev
# http://127.0.0.1:8094
./neokit init /path/to/new-app
./neokit build --app /path/to/new-app --out build/new-app
./neokit serve --app /path/to/new-app --out build/new-app --no-build
```

`./web` remains a compatibility alias. Sources use `.neo`, with `neo.toml` as the package manifest and `web.toml` as framework configuration. `NEO_THREADS` selects native workers: the server uses every CPU by default, and `./neokit dev` runs one. With more than one worker, a supervisor process replaces crashed workers, so `NEO_THREADS=4` runs five processes (256 workers at most). `NEOKIT_LIBRARY_CACHE=off` disables prepared dependency objects; otherwise they are shared under `~/.cache/neokit/native`. Release builds use LLVM O3. Development builds keep application optimization low while retaining type, effect, ownership and runtime checks.

## Showcase applications

Run `bun examples/run.ts` to build and start three local native previews:
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
app/(marketing)/pricing/          (group) folders share layouts, not URLs
app/feed.xml/route.toml           dotted segments for files such as feeds
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

More page options:

- `guard="signed_in"` names an `effect fn(WebRequest) -> WebResponse` that runs before the loader. `web_continue()` lets the page render; any other response is sent instead, and an empty 404 shows `not-found.view`. Guarded pages are never exported.
- `paths_fn="all_posts"` names a function returning `Arr (Arr u8 _) _` that lists a dynamic route's pages at build time, like `generateStaticParams`. It can be combined with `paths`. A `mode="static"` dynamic route serves exactly the listed paths and answers 404 for others.
- `cache_control="private, max-age=30"` sets the header of an uncached SSR page; other SSR pages send `no-store`.

`web.toml` can declare redirects. They are served natively, listed in the manifest and written to `_redirects` in exports:

```toml
[[redirects]]
from="/old-pricing"
to="/pricing"          # or an absolute https:// URL
status=308             # 301, 302, 303, 307 or 308; 308 by default
query=true             # keep the request query string; true by default
```

Every response carries a `Date` header and, by default, Neo's Content-Security-Policy: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'`. `web.toml` can replace it for the whole site, including guard responses, redirects and error pages:

```toml
[security]
csp="default-src 'self'; img-src 'self' https://cdn.example"
```

A response that sets its own `Content-Security-Policy` keeps it. Streamed responses get the site policy too: async pages (`web_stream_page`), other `stream=true` handlers and event streams return a head Neo wrote, and the server replaces Neo's default policy in it, or adds the site policy to a head without one (Neo writes none for event streams). A stream's own policy, `Content-Security-Policy-Report-Only` and the body are left as they are. The 504 and 503 replies Neo's runtime writes when an async page or API misses its deadline or fails before replying still carry Neo's default. Exports write the policy to `_headers`, which Netlify and Cloudflare Pages apply.

A buffered `route.toml` can allow cross-origin requests with `cors=["https://app.example"]` or `cors="*"`, optional `cors_headers=["content-type","authorization"]` and `cors_credentials=true` (not with `*`). NeoKit answers the OPTIONS preflight and adds `Vary: Origin`. Typed actions need `csrf=false` and their own authentication before they can be cross-origin.

Endpoints return `WebResponse`; `methods=["POST","PATCH","DELETE","OPTIONS"]` explicitly selects methods. GET automatically permits HEAD, and mismatches return 405 with Allow. Helpers provide status codes, safe response headers, relative redirects, URLencoded forms, cookies and multipart parsing. Native request framing accepts Content-Length or chunked bodies and answers conflicting framing with 400, headers over 16 KiB with 431 and input over about 16 MiB with 413. Each worker serves up to `NEO_HTTP_CONNECTIONS` connections (1,024 by default) and answers more with 503 and `Retry-After`. Slow clients are disconnected: headers must arrive within `NEO_HTTP_HEADER_TIMEOUT_MS` of the first byte (10 s, then 408), and the server waits at most `NEO_HTTP_BODY_TIMEOUT_MS` between body reads (30 s), `NEO_HTTP_IDLE_TIMEOUT_MS` between keep-alive requests (60 s) and `NEO_HTTP_WRITE_TIMEOUT_MS` between response writes (30 s). A value of 0 disables that timeout. Multipart upload handling is buffered and bounded, rather than a direct-to-storage streaming upload.

`[server] before="guard"`, `after="decorate"` names functions imported from `app/hooks.neo`. A before hook returns `web_continue()` or a response. After hooks decorate buffered responses. Streaming handlers own the bytes and headers after the before hook, apart from the `[security] csp` policy in their head; buffered after/error hooks cannot rewrite an already-started stream.

## Templates and actions

```html
@props(title:text, names:text[], destination:url)
<h1>{{ title }}</h1>
<a href="{{ destination }}">Continue</a>
<ul>{% for name in names %}<li>{{ name }}</li>{% endfor %}</ul>
{% include Badge label=title %}
{% component Card title=title %}<p>Checked content slot.</p>{% endcomponent %}
```

Types are `text`, `int`, `bool`, trusted `html` (`WebHtml`), `url` (`WebURL`) and corresponding scalar arrays other than URL arrays. `web_url("/relative/path")` constructs a checked relative URL; an invalid URL becomes `#`. Text and quoted attributes are escaped. URL-valued attributes (`href`, `src`, `action`, `poster`, `data`, `xlink:href` and the rest) require a URL prop. Includes, slots, nested loops and conditions are checked during compilation.

```html
@props(posts:Post[], query:text, page:int)
@type Post(title:text, href:url, views:int)
{% if query %}<p>Results for {{ query }}</p>{% endif %}
{% for post, index in posts %}
  <a href="{{ post.href }}" data-rank="{{ index }}">{{ post.title }}</a>
  {% if post.views >= 1000 %}Popular{% elif post.views == 0 %}New{% endif %}
{% endfor %}
```

`@type` lines after `@props` describe the fields of a Neo record the loader defines, so lists of records render without parallel arrays. Conditions accept bools, non-empty text or lists (`{% if !items %}`), and comparisons of ints (`== != < <= > >=`) or text (`==`, `!=`) against a literal or another value of the same type. `{% elif %}` chains branches. `for item, index in list` binds a zero-based index. A slot component declares `content:html`. Arbitrary expressions, dynamic executable attributes and script/style interpolation are rejected. Trusted HTML remains an explicit application responsibility.

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

`examples/dashboard` is a functioning task app with login/logout, signed sessions, CSRF checks, per-user reads and CRUD, progressive forms, JSON APIs and restart persistence. Password verification uses the reusable native PBKDF2-HMAC-SHA256 library with a 600,000-round minimum. Session cookies are HttpOnly, SameSite=Lax and Secure for an HTTPS origin. Logout revokes all of that user's sessions, on every device and including copied cookies; revocation times live in `$NEOKIT_DATA_DIR/sessions`. Login allows ten attempts a minute per client address, with an account-wide ceiling of thirty. Behind a reverse proxy, set `NEO_TRUSTED_PROXIES` to the proxy's addresses so the limit applies to the forwarded client rather than the proxy.

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

The reusable PostgreSQL package supports native v3 wire connections over caller-owned TCP/TLS, trust or SCRAM-SHA-256 auth, parameterized extended queries, SQL error recovery and bounded connection-pool bookkeeping. Results preserve text/NULL values rather than coercing decimal or large integers. The caller supplies credentials, verified transport, deadlines and resource cleanup. See [database profile](../neo-libs/postgres/README.md). The reusable SELECT/INSERT/UPDATE/DELETE builder quotes checked identifiers, binds values separately, supports null-safe equality filters and requires explicit mutation predicates. There is no ORM, MD5 auth, COPY or binary-column codec. A standalone independent protocol peer tests authentication, framing, injection-shaped parameters and reuse; The async suite also exercises a real PostgreSQL server using SCRAM.

Optional provider login uses native OAuth2 authorization-code flow with PKCE S256, signed provider-bound state, a browser challenge cookie and authenticated HTTPS userinfo. Configure `NEOKIT_OAUTH_AUTHORIZE_URL`, `NEOKIT_OAUTH_TOKEN_URL`, `NEOKIT_OAUTH_USERINFO_URL`, `NEOKIT_OAUTH_CLIENT_ID`, optional `NEOKIT_OAUTH_CLIENT_SECRET`, `NEOKIT_OAUTH_SCOPE` and `NEOKIT_OAUTH_SUBJECT_CLAIM` (default `sub`). Register the exact `${NEOKIT_ORIGIN}/oauth/callback` URL. Providers must expose a stable subject in userinfo. Set both `NEOKIT_OIDC_ISSUER` and `NEOKIT_OIDC_JWKS_URL` to enable native RS256 ID-token/JWKS validation with exact issuer/audience, expiry, nonce and matching userinfo subject. This requires `openid` scope and the `sub` claim. [The shared library profile](../neo-libs/jwt/README.md) lists supported algorithms and limits. No real provider account was configured during these tests.

## Caching, streaming and assets

Public SSR pages can set `cache_ttl=60`, `stale_ttl=300`, `cache_tag="catalog"`. The shared native file cache publishes entries atomically; keys include build identity, path, tag generation and the query parameters listed in `cache_query=["page","category"]`. Without `cache_query`, a request with any query string bypasses the cache, so arbitrary URLs cannot fill the cache directory. Cached responses send `Vary: X-Web-Fragment` so shared caches keep full documents and fragments apart. Requests with cookies or Authorization bypass the cache. `web_cache_invalidate_tag` invalidates captured tag generations without publishing a racing stale render under a new generation. HIT, MISS and STALE responses identify cache behavior. Static routes and public caches must not contain private data.

A stale hit returns existing HTML and enqueues native background revalidation. Run `./neokit revalidate --app … --out …` alongside the server, with the same `NEOKIT_CACHE_DIR`, a random `NEOKIT_REVALIDATE_TOKEN` of at least 32 bytes, and `NEOKIT_REVALIDATE_HOST`/`NEOKIT_REVALIDATE_PORT` (127.0.0.1/8094 by default). The queue is bounded, deduplicated briefly and drained by a separate native worker. A crashed worker can lose a drained job; hard expiry still forces a fresh render. The cache keeps at most 4,096 entries of up to 8 MiB each, in fixed slot files, and the revalidation worker deletes expired entries about once a minute. When upgrading from a NeoKit that predates neo-libs, empty `NEOKIT_CACHE_DIR`: files in the old format are never read or removed.

Pages and APIs can opt into `async=true`: native database and network waits suspend the loader while its worker serves other requests. A `stream=true` endpoint can send a shell and independent deferred HTML sections, with cancellation, deadlines and bounded cooperative backpressure. `async=true` also allows the stream handler to wait before sending its shell. See [async rendering and streaming](docs/async-rendering.md), [the PostgreSQL/network example](examples/features/app/async/route.neo), and [the raw chunk example](examples/features/app/stream/route.neo). This is an HTML/widget model, without React/RSC compatibility.

`[site]` and `[metadata]` support description, robots, OpenGraph fields and canonical URLs. Per-request metadata comes from loader props: `[metadata.props] description="summary"`, `og_title`, `og_description`, `robots`, `og_image` and `canonical` (url props, joined to `[site] url`) and `jsonld` (a text prop holding JSON, written as an escaped `application/ld+json` block). Dynamic metadata needs a `head:html` layout prop rendered inside `<head>` as `{{ head }}`; static metadata goes there too when the layout declares it, or before the first `</head>` otherwise. A configured public site URL generates robots.txt and a sitemap from explicit public export paths, unless `public/` provides its own. `[images] files=["photo.jpg"], widths=[320,640,1280], formats=["webp"]` generates content-hashed responsive variants at build time using libvips (`vips` and `vipsheader`), cached by content in the build directory so unchanged images are not resized again. Bind a generated URL with `@asset(image:photo.jpg:640:webp)`. Widths above the original are clamped. There is no on-demand image server.

Embedded files send an ETag and answer `If-None-Match` (weak comparison, lists and `*`) with 304 responses that keep their cache headers. Text assets are precompressed with gzip only (there are no Brotli variants) and served compressed when the request accepts gzip, honoring `q=0`. Hashed `/_web/` assets are immutable for a year; public files, the stable `neokit-client.js` entry and static pages use `public, no-cache` so browsers revalidate them cheaply. `/healthz` provides readiness; `[server] access_log=true` emits JSON request timing/status to stderr and adds request IDs to buffered replies. Stream logs cannot infer a buffered status.

## Browser behavior and development

Realtime endpoints opt into `realtime=true`; native applications publish named
JSON events to bounded channels and browsers subscribe through a small helper
or `context.subscribe` in a widget. Channels share replay history across a
server's workers, with reconnect, heartbeat and cleanup support. The backend is
ephemeral shared memory, with no Redis. Publications wake workers through their
kernel-watched IPC sockets; subscriptions do not poll channel data. A startup
hook can register an independent demo producer using Neo's reusable timers.
See the [API, limits and SaaS example](docs/realtime.md).

Widgets export `mount(element,context)` and may return cleanup. The runtime lazily loads registered widget modules, passes parsed props and shared state/action helpers, and disposes replaced widgets. `signal`, `state` and `computed` in `client/state.js` support subscriptions and stable keyed state. `createAction` tracks pending/errors and supports optimistic rollback.

`data-web-link` enables same-origin navigation, history, scroll, `#fragment` targets, server redirects and metadata (including JSON-LD) updates. Shared nested layouts and explicitly persistent nodes retain their DOM identity; replaced page widgets clean up. Navigation links get `aria-current="page"` on an exact match, or for pages below them with `data-web-match="prefix"` or an explicit `data-web-match="/section"`. Elements outside the page marked `data-web-sync="name"` copy their text from the next page. `<html data-web-transitions>` animates navigations with the View Transitions API where browsers support it.

`data-web-prefetch` opts public pages into bounded prefetch; in-flight and uncacheable pages are not fetched twice, cacheability headers are respected and successful mutations invalidate prefetched HTML. `data-web-form` enhances GET and POST forms with normal browser fallback; `data-web-pending`, `data-web-success` and `data-web-failure` set the text of the form's `[data-web-status]` element. `data-web-load="visible"` or `"idle"` on a widget delays loading its module. Apps may use `client/index.ts` or `.tsx` as the browser entry. JavaScript-free pages remain possible. This is an islands/widgets model, without a general React-style component renderer.

Development watches source/CSS/browser changes, retains the old server on failed builds, exposes a compile-error overlay and refreshes connected pages after a successful build. Development builds serve `public/` and bundled assets from disk under stable names with source maps, so CSS and browser-module edits rebundle without recompiling the server: stylesheets swap in place and modules hot-reload. Browser-only edits use a hot module path with preserved keyed stores; other edits restore nonsecret form drafts across reload. This behavior has DOM-level tests and actual-browser checks (in `neokit-bench`), including preserved state, cleanup, hot updates, visible compile errors and draft recovery.

Tailwind is optional: add `[css] tailwind=["site.css"]` to `web.toml`, then use `@import "tailwindcss" source(none);` and explicit `@source` paths in `styles/site.css`. The frozen Bun lockfile pins its build tools. Complete utility names are required. Plain CSS apps need no Tailwind dependency.

## Export, verification and deployment

```sh
./neokit export --app examples/studio --out build/static --dest build/export
./neokit preview --dir build/export
./neokit test
./neokit test --extended --postgres
```

Export renders every eligible page in one native process with `server=false`, empty query/body and timestamp zero, and writes `404.html` from `not-found.view`. `export=false`, guarded pages and API routes are omitted; `--strict` rejects omissions. Exports contain ordinary HTML/CSS/JS and replace the previous export only after a complete copy. The integration tests are Bun tests that drive only the `neokit` CLI, with shared helpers in `tests/lib.ts`: `./neokit test` runs `tests/core`, `--extended` adds the dev-watcher, large-response, realtime and showcase suites in `tests/extended`, and `--postgres` runs the async suite in `tests/postgres` against a private PostgreSQL cluster that `tests/tools/postgres.ts` creates with `initdb` and `pg_ctl`. One file runs with `bun test --timeout 600000 ./tests/extended/dev_css.test.ts`. Use `export=false` for authenticated pages. Custom 5xx templates handle returned error responses, not arbitrary runtime traps or nested exception boundaries.

The scratch-container recipe embeds assets and includes native healthcheck/migration binaries and a CA bundle for outbound HTTPS. The Linux binary is intended to have no libc, JS engine or shared-library imports; The dashboard passes the real integration in ARM64 Linux scratch and x86-64 Linux scratch under emulation, with nonroot UID, read-only root, dropped capabilities and Docker init. Both ELF builds have no interpreter, dynamic dependencies or undefined symbols. The full production build pipeline and public deployment remain separate checks. macOS native binaries are checked for undefined symbols. TLS ingress uses Caddy. The native server stops accepting on SIGTERM, drains active responses to its configured grace deadline and cancels outstanding async work.

```sh
docker build --build-context neo="$NEO_HOME" --build-context neo-libs=../neo-libs -t neokit .
# For the dashboard example, create deploy/.env from .env.example, then:
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --entrypoint /migrate app
docker compose --env-file deploy/.env -f deploy/compose.yaml up --build -d
```

The checked-in Compose example runs the dashboard, persistent volumes, a read-only app container with init for PID 1 signal forwarding, native readiness probes and a TLS proxy. A stale-cache application can add `-f deploy/revalidation.compose.yaml`, set `NEOKIT_APP` and `NEOKIT_REVALIDATE_TOKEN`, and run the packaged native worker sharing its cache volume. No cloud-specific adapters, edge-runtime distribution, distributed cache coordination, deployment rollbacks or hosted control plane are implemented.

## Remaining gaps

Compared with Next.js and Astro, these are not implemented: Markdown/MDX content collections, i18n routing, an on-demand image server, incremental static regeneration of exported pages, edge or serverless adapters, React/Vue/Svelte component islands, HTTP/2, WebSockets and byte-range responses.

## Reusable foundations and performance

Common functionality was added below NeoKit first: Neo standard-library HTTP server parsing/headers/forms/cookies/routing, shared transport types, chunked input, typed action input, prepared HMAC/PBKDF2 and request logging. `neo-libs` owns independently reusable web-security, web-cache, document-store, postgres, jwt and oauth2 packages. NeoKit composes these libraries rather than implementing private copies.

Benchmark scripts and results live in the separate `neokit-bench` repository. They compare production NeoKit builds with Next.js and Astro on uncached and authenticated SSR, durable mutations, realtime delivery, large lists, async rendering and build speed, and keep raw samples and the exact versions measured. They describe those builds on that hardware, not frameworks in general.

Large-list rendering uses a [shared windowed renderer and inert native page data](docs/windowed-lists.md). The 10,000-record browser fixture keeps a bounded DOM, preserves focused drafts, and includes an explicit full-DOM diagnostic.
