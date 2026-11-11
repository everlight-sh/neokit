# NeoKit expansion verification — 2026-11-07

The local framework is `/Users/marcoschwartz/Documents/code/neokit`.
The language checkout remains named `axon`; reusable source packages are in
`axon-libs`. No production Apteva component was changed or deployed.

## Implemented and exercised

- Nested layouts; multiple named parameters; required/optional terminal
  catch-alls; precedence across pages and API routes; custom 404/returned-5xx views.
- Checked scalar/list/URL template props, nested loops, component content slots,
  escaping and build-time type validation.
- HTTP methods and Allow responses; JSON/form action schemas; bounds and field
  validation; before/after middleware; bounded multipart and chunked uploads.
- Signed sessions, PBKDF2 passwords, CSRF and account-wide rate limiting;
  per-user authorization in a working task dashboard.
- Native, file-locked document transactions, full file/directory synchronization,
  atomic publication and explicit idempotent migrations.
- Reusable PostgreSQL wire/SCRAM client, parameterized CRUD query builder,
  connection reuse and bounded pool bookkeeping. An independent Python peer
  checks proofs, fragmented packets, injection-shaped values, NULL, error
  recovery, rejected statements and lease expiration.
- Reusable OAuth2 authorization-code/PKCE flow with browser/provider-bound state
  and authenticated userinfo; optional native RS256/JWKS OIDC ID-token verification. A typed fixture checks the protocol; NeoKit's
  HTTPS driver uses the native TLS client and system roots.
- Atomic tagged page caching, personalized-request bypass, bounded revalidation
  queue and a separately executable native background worker. Build identities
  include source manifests/locks, language helpers and compiler sources.
- Genuine chunk flushing before delayed data, deadlines/backpressure handling
  and correct ordering after an earlier pipelined buffered response.
- Browser signals, stable state, action pending/error state, optimistic rollback,
  layout/node retention, widget cleanup, metadata replacement and public prefetch.
- Development rebuilding, error reporting, previous-server retention on build
  failure, client hot-update/reload paths, scaffolding and toolchain diagnostics.
- Build-time responsive images and metadata; robots/sitemap; gzip/ETag assets;
  native healthcheck, structured request logs, migration binary, scratch-image
  recipe and Compose/Caddy deployment files, including a revalidation overlay.

## Commands and evidence

```sh
# Framework: 20 Python tests, browser/runtime tests, native SQL
# builder, HTTP/export, independent consumer/scaffold, features and dashboard.
NEOKIT_LIBRARY_CACHE=off ./neokit test
NEOKIT_LIBRARY_CACHE=off python3 tests/dev_css_smoke.py

# Language regressions, from ../axon:
python3 -m unittest discover -s tests -p 'test_packages.py'
python3 -m unittest discover -s tests -p 'test_modules.py'
python3 tests/native_library_cache.py
python3 tests/http2_language.py
python3 tests/http_handoff.py --requests 4096

# Library peer, from ../axon-libs:
python3 tests/postgres_peer.py
python3 tests/test_jwt.py

# From neokit, with matching Linux ELF artifacts and Docker running:
python3 -m tests.linux_container --out build/linux/dashboard --platform linux/arm64
python3 -m tests.linux_container --out build/linux-x86/dashboard --platform linux/amd64
```

Standalone native HTTP-server, PBKDF2/prepared-HMAC, web-security/cache and OAuth2
fixtures also compile and run. Both monolithic and prepared-library builds pass
the features and authenticated dashboard integrations. The full native HTTP/2/TLS integration passed,
including custom authenticated request headers and HTTP/1.1 fallback. Native
dashboard tests perform real 600,000-round password checks, reject bad CSRF and
other-user access, persist 24 concurrent mutations across two workers, and
restart the server against the same database. The native migrator runs twice
without changing an initialized schema; the compiled healthcheck reaches the
server. Source-package locks verify successfully.

The macOS kqueue connection path now keeps accepted sockets with their accepting
worker. Its independent regression reproduces a dropped connection with the
previous backend and passes 4,096 short-lived concurrent connections each with
two and four workers on the revision. Sixty complete dashboard stress runs also
pass. [The comparison record](../bench/browser-results/2026-11-07/ownership-regression.json)
preserves the evidence.

Both `aarch64-unknown-linux-gnu` and `x86_64-unknown-linux-gnu` generated feature
programs pass Neo's checker. Compose base and revalidation overlay parse with
`docker compose … config --quiet`. These checks do not substitute for running
the Linux image. Native macOS HTTP tests audit undefined symbols.

[Performance report and raw results](../bench/parity-results/2026-11-07/REPORT.md)
cover 78 completed production trials, with independent semantic checks and
recorded binary SHA-256 identities. The report compares final catalog throughput
against the saved baseline. The authenticated task page has a large
SSR/memory advantage over the installed Next 16.1.1 fixture. Fully flushed writes
are storage-bound and have smaller throughput gains plus latency tradeoffs.
The report records short-trial, payload, host-load and nearly-full-disk limits.

## Remaining verification and product limits

- [Actual-browser verification](../bench/browser-results/2026-11-07/REPORT.md)
  passes navigation/state/cleanup, forms, authentication, hot updates, compile
  errors and draft recovery, plus real focus/caret checks at 1,000 and 10,000
  rows. The default 10,000-record view now uses a bounded window and passes
  [follow-up browser checks](../bench/browser-results/2026-11-07-windowed/REPORT.md).
  The explicit unvirtualized diagnostic still stalls. These are not controlled
  browser paint benchmarks.
- The follow-up checks run the real dashboard integration in Linux ARM64 scratch
  and x86-64 scratch under emulation. Read-only root, nonroot UID, dropped
  capabilities and Docker init pass. The full Ubuntu/Bun production-image build
  and public deployment remain unverified. Compose now enables init for correct
  PID 1 signal forwarding; native request draining remains unimplemented.
- PostgreSQL 17.11 now passes real-server checks from native macOS and Linux
  clients. OIDC signature and full exchange checks use independent signed
  fixtures; no real OAuth provider credentials were used.
- Streaming occupies its worker; there is no async rendering scheduler, RSC,
  React Suspense or full React component compatibility. Returned error responses
  can use a custom view; runtime traps are not nested exception boundaries.
- The embedded store is bounded JSON, not SQLite. The PostgreSQL builder is a
  small parameterized CRUD API, not an ORM. OAuth supports optional native RS256 ID-token/JWKS validation
  with a fixed issuer, audience, browser-bound nonce and matching userinfo.
  Discovery, refresh-token lifecycle and durable key caching remain absent. Cache storage has per-entry/queue bounds, with
  operator-managed total disk usage.
- Caddy provides TLS ingress and full-stack shutdown draining. The raw native
  server still lacks graceful SIGTERM draining. Cloud/edge adapters, distributed
  cache coordination and deployment rollbacks are not implemented.

This is a substantial functional framework expansion, not complete Next.js
feature parity. Tests and measurements apply to the profiles documented here.

## Follow-up: bounded lists, OIDC and Linux execution

See [the follow-up report](../bench/browser-results/2026-11-07-windowed/REPORT.md),
[windowing API](windowed-lists.md), and
[latest performance matrix](../bench/parity-results/2026-11-07-windowed-oidc/REPORT.md).
The reusable inert JSON writer and unique-member check live in Neo. JWT/OIDC
lives in `axon-libs/jwt`, used first by `axon-libs/oauth2`, then NeoKit.
A Darwin → same-architecture Linux linker selection fix now uses lld for ELF.
Both Linux binaries pass undefined-symbol, interpreter and dynamic-dependency
audits. The existing VM was returned to its stopped state after checks.
