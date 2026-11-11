# Changelog

## Unreleased

- Neo 0.2.2: the runtime is compiled once per machine, and SDK releases expect the 0.2.2 archive.
- Static export renders every page in one process and writes `404.html`, `_redirects` and dotted files such as `feed.xml`.
- Server builds swap executables atomically. Development serves assets from disk, so CSS and browser edits skip the native rebuild.
- Routing: `(group)` folders, dotted segments, page `guard`, build-time `paths_fn`, `[[redirects]]` and CORS for API routes.
- Templates: `elif`, comparisons, text and list truthiness, loop indexes and `@type` records. Layout substitution no longer rewrites string literals. More URL attributes and meta refresh are checked.
- Caching: `cache_query` keys, a query bypass without it, `Vary: X-Web-Fragment`, and `cache_control` for uncached pages.
- Assets: weak ETag matching, 304 replies that keep cache headers, q-value-aware gzip and optional Brotli, revalidated static pages, and a content cache for responsive images. `public/` sitemap and robots files take precedence.
- Metadata from loader props, including JSON-LD, through a `head:html` layout prop.
- Browser runtime: redirect-aware navigation, hash scrolling, generic `aria-current` and `data-web-sync`, configurable form status text, deduplicated prefetch, lazy widgets and optional view transitions. Studio-specific strings were removed.
- CLI: `serve` uses every CPU, dev reports server crashes and detects deleted files, `stop` escalates to SIGKILL, `migrate` and `revalidate` default to the current app, and `test --extended`.
- Tests: `tests.audit_features_smoke` compiles one app that uses every routing, template, caching and metadata option above, then checks its server and export. `./neokit test` runs it.
- Deploy: pinned Caddy, compression and security headers, HSTS for public domains, log rotation, memory limits, a Docker healthcheck, Pillow and Brotli in the image, and a CI unit-test workflow.

## 0.1.0-alpha.1

- Relocatable source SDK with the Neo compiler, locked libraries, browser runtime, and installer.
- Standalone app scaffolding and a read-only Meridian deployment behind HTTPS.
- Native SSR, static exports, typed templates and actions, async loaders, streamed sections, and same-host SSE replay.
