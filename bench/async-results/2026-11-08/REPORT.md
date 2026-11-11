# Native async rendering and streaming — 2026-11-08

## What changed

Neo now provides reusable cooperative byte-result tasks with guarded native stacks, scoped cleanup, cancellation, deadlines, bounded admission and fair ready-task turns. Native socket waits suspend their call stacks, including HTTP, verified HTTPS, DNS and the existing shared PostgreSQL client. Contended advisory file locks also yield. The HTTP executor uses its existing kqueue/epoll readiness backend; no JS engine, libc, Redis or background thread pool implements the runtime.

Neo’s shared HTTP layer adds buffered async responses, atomic fan-out admission, raw incremental writes, atomic framed results, bounded backpressure, error fallbacks and root-loader-to-stream header handoff. Client disconnects cancel outstanding waits even before any upstream bytes arrive. Shutdown drains to the grace deadline and then cancels remaining work.

NeoKit adds `async=true` / `async_timeout_ms` to pages and endpoints, composable `web_defer` / `web_boundary` / `web_stream_page` sections, and an external client module that mounts arriving widgets. Fragments resolve independently, use request-scoped tokens and completion markers, and reject executable markup. Existing typed templates keep escaping text. The ordinary nonstreaming browser path does not install a streaming observer.

[Usage and limits](../../../docs/async-rendering.md), [the complete real-PostgreSQL/network example](../../../examples/features/app/async/route.neo), [the reusable Neo APIs](../../../../axon/docs/async-rendering.md).

## Verification

The final wire-level run sent its shell after 34.5 ms, its fast network section after 118.3 ms, its database section after 655.1 ms and its slow section after 1235.8 ms. The deliberately short section deadline produced an isolated error. Thirty fast HTTP requests during unrelated loader waits completed in 9.5 ms. Task/capture/result counters returned to zero after disconnect/deadline tests.

Actual-browser checks deliberately used longer independent delays: the fast network, PostgreSQL and slow network sections appeared around 1.3 s, 4.1 s and 6.1 s. The inserted counter worked (0→2), static export worked (0→2→0), and the final browser check had no warnings/errors. Native stress tests also exercised eight slow readers receiving 8 MiB each, complete chunk framing, 256-task capacity, admission rollback, timeout-before-headers, late child timeouts, repeated reuse and grace shutdown.

24 browser-client tests, 21 NeoKit compiler tests and 18 focused Neo compiler tests pass. The prepared-library cache integration passes including a callback executed from a prepared dependency. Native HTTP/TLS/DNS/task/backpressure tests pass on macOS and ARM64 Linux; a basic x86-64 Linux task fixture passes under emulation. Real PostgreSQL application checks pass with 1/2/4 workers on macOS and with one worker on ARM64 Linux. Existing feature/dashboard integrations pass. [Full validation matrix](VALIDATION.md), [source/executable identities](identities.json), [browser observations](browser-final.json).

![Actual browser: independently streamed sections and working counter](browser-streaming.png)

## Production performance

Medians from three shuffled rounds of matched uncached SSR, one worker/process, one wrk thread and 32 connections on this shared Apple M1 Pro Mac. Next 16.1.1, Astro 7.4.7 / Node adapter 11.1.11, Node v25.7.0. NeoKit uses production prepared libraries.

| Framework | Small SSR req/s | p99 ms | Peak sampled RSS MiB | CPU µs/request | CPU % at equal 200 req/s |
|---|---:|---:|---:|---:|---:|
| NeoKit | 86,224 | 0.910 | 4.7 | 9.40 | 0.83 |
| Next | 934 | 61.150 | 369.3 | 1269.67 | 47.51 |
| Astro | 8,721 | 12.380 | 302.3 | 158.12 | 12.84 |

The small SSR fixture measured NeoKit at 92.3× Next throughput and 9.9× Astro throughput. The complete [63-trial matrix](../../core-results/2026-11-08-async/REPORT.md) includes single-connection latency, 1,000-row SSR, 50-row interactive SSR, startup, payload sizes, CPU and raw samples. These are local fixture results, not universal framework rankings or feature-equivalence claims.

| Concurrent background work | Clients | Fast-route req/s | Fast-route p99 ms | Peak sampled NeoKit RSS MiB |
|---|---:|---:|---:|---:|
| idle | 0 | 114,809 | 0.568 | 4.8 |
| network-waits | 32 | 113,099 | 0.483 | 6.8 |
| database-and-network-streams | 16 | 135,707 | 4.140 | 7.2 |

The fast route here is `/healthz`, not the SSR workload above. The database/stream case includes real SCRAM authentication and `pg_sleep(0.6)`, with independent HTTP waits. Every trial drains back to zero task counters. Server CPU includes background work; PostgreSQL and peer resources are excluded from server RSS/CPU. Host scheduling noise is visible in individual trials, including one 24.9 ms idle p99 outlier; the higher mixed-work throughput does not establish that background work improves performance. [All nine mixed-load trials](mixed/REPORT.md).

The authenticated 100-owned-task file-backed page measured 6,561 req/s prepared versus 6,622 monolithic (-0.9%), and 7,149 on the saved pre-async prepared binary (-8.2%). The prior prepared-only deficit is not reproduced, but this run shows an overall cost versus the saved pre-async executable; it must not be described as zero regression. Compiler/runtime differences and code layout are included, so these measurements do not isolate scheduler overhead. [15 shuffled authentication trials and identities](../../core-results/2026-11-08-async/PREPARED.md).

No builds or browser checks ran alongside the final timing matrix. All processes share a busy Mac; there is no affinity/thermal control or formal confidence interval. CPU is process-group time from `ps`, with 10 ms resolution. RSS is sampled every 250 ms and can miss short peaks. Node background threads may exceed one CPU core. Next includes RSC serialization/bootstrap costs; NeoKit and Astro use HTML plus widgets. Full response semantics are checked before/after each trial. Browser loading/hydration and database workloads are separate from the cross-framework SSR matrix.

## Remaining limits

Scheduling is cooperative: pure CPU loops must yield/checkpoint. Regular disk reads, writes and fsync remain synchronous, although contended locks yield. PostgreSQL results are buffered, not cursor/row streaming; cancelling a local wait closes its socket and does not promise server-side query cancellation. Async HTTP/1.1 responses close their connection, and client EOF/half-close cancels an async response. Enhanced-link navigation falls back to a document load for streams; the existing client router buffers its fetch. Async access logs cover admission rather than completed delivery. This is not React/RSC or full Next.js compatibility.

Admission is bounded at 256 tasks per worker / 32 sections per response. Each reserves a guarded 1 MiB stack; copied captures and result buffers have explicit per-task and aggregate limits. Pending userland response output is limited to 64 KiB per connection. Logical budgets are not total-process RSS guarantees.
