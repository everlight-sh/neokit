# Changelog

## Unreleased

- Neo 0.2.6: SDK releases expect the 0.2.6 archive. Streamed async pages now send a `Date` header.
- Neo 0.2.5: the runtime is compiled once per machine, and SDK releases expect the 0.2.5 archive. The server now times out slow clients, answers bad requests with 400/413/431, restarts crashed workers and reads trusted `X-Forwarded-For`.
- Responses carry a `Date` header. `[security] csp` in `web.toml` sets the site's Content-Security-Policy, and exports write it to `_headers`. The README lists the `NEO_HTTP_*` connection limit and timeouts.
- Libraries come from neo-libs 0.1.0 (`../neo-libs`, formerly axon-libs); every package is named `neo-*`. NeoKit no longer looks for a toolchain in `../axon`.
- Sessions: `web_session_read` takes a revocation directory. The dashboard's logout revokes all of a user's sessions, including copied cookies.
- Login is rate-limited per client address (`web_rate_limit_client`) with an account-wide ceiling. Set `NEO_TRUSTED_PROXIES` behind a proxy.
- The page cache is bounded to 4,096 slot files, and the revalidation worker sweeps expired entries. Upgrading: empty `NEOKIT_CACHE_DIR`, since files in the old format are never read or removed.
- Static export renders every page in one process and writes `404.html`, `_redirects` and dotted files such as `feed.xml`.
- Server builds swap executables atomically. Development serves assets from disk, so CSS and browser edits skip the native rebuild.
- Routing: `(group)` folders, dotted segments, page `guard`, build-time `paths_fn`, `[[redirects]]` and CORS for API routes.
- Templates: `elif`, comparisons, text and list truthiness, loop indexes and `@type` records. Layout substitution no longer rewrites string literals. More URL attributes and meta refresh are checked.
- Caching: `cache_query` keys, a query bypass without it, `Vary: X-Web-Fragment`, and `cache_control` for uncached pages.
- Assets: weak ETag matching, 304 replies that keep cache headers, q-value-aware gzip and optional Brotli, revalidated static pages, and a content cache for responsive images. `public/` sitemap and robots files take precedence.
- Metadata from loader props, including JSON-LD, through a `head:html` layout prop.
- Browser runtime: redirect-aware navigation, hash scrolling, generic `aria-current` and `data-web-sync`, configurable form status text, deduplicated prefetch, lazy widgets and optional view transitions. Studio-specific strings were removed.
- CLI: `serve` uses every CPU, dev reports server crashes and detects deleted files, `stop` escalates to SIGKILL, `migrate` and `revalidate` default to the current app, and `test --extended`.
- Tests: `tests/core/audit_features.test.ts` compiles one app that uses every routing, template, caching and metadata option above, then checks its server and export. `./neokit test` runs it.
- Tests: the integration suites are Bun tests that drive only the `neokit` CLI: `./neokit test` runs `tests/core`, `--extended` adds `tests/extended`, and the new `--postgres` runs `tests/postgres` in a private PostgreSQL cluster (`tests/tools/postgres.ts`). The browser, empty-root and container tools are in `tests/tools`.
- Deploy: pinned Caddy, compression and security headers, HSTS for public domains, log rotation, memory limits, a Docker healthcheck, Pillow and Brotli in the image, and a CI unit-test workflow.

## 0.1.0-alpha.1

- Relocatable source SDK with the Neo compiler, locked libraries, browser runtime, and installer.
- Standalone app scaffolding and a read-only Meridian deployment behind HTTPS.
- Native SSR, static exports, typed templates and actions, async loaders, streamed sections, and same-host SSE replay.
