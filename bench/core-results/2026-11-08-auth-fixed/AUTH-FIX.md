# Authenticated-rendering performance fix

The fixed prepared build measured **7,671 authenticated renders/sec**, versus **6,606** for the regressed async build (+16.1%) and **7,139** for pre-async (+7.5%). It beat pre-async in 7/7 paired rounds. The previously observed 8.2% throughput deficit is eliminated in this fixture. CPU per request changes by -13.8% versus regressed async.

## Reusable fixes in Neo

The compiler constrains expensive forced-inline calls locally instead of removing a helper’s policy globally. Prepared optimization hints precede object-local constraints, and the application uses its configured expansion budget. Neo’s JSON decoder measures an exact output size and copies ordinary text in runs, avoiding byte-by-byte temporary arrays. Authentication, owner filtering and the application data path are unchanged. [Implementation and rationale](FIX.md).

## Authenticated file-backed SSR

7 paired rounds, five measured seconds after a one-second warmup; one worker, 32 connections. Every trial validates 100 owner-filtered rows from a 120-task file, escaping/UTF-8, CSRF, session behavior and durable mutations before/after timing.

| Build | Median req/s | CPU µs/request | Peak sampled RSS MiB |
|---|---:|---:|---:|
| Pre-async prepared | 7,138.6 | 137.61 | 3.5 |
| Regressed async prepared | 6,605.7 | 147.71 | 3.4 |
| Fixed async prepared | 7,670.9 | 127.33 | 3.5 |
| Fixed async monolithic | 7,966.5 | 122.66 | 3.5 |

The ratio of aggregate medians versus pre-async is +7.5%; the median within-round ratio is +9.9%. Both are reported because shared-host load varies. Prepared versus the rebuilt monolithic control is -3.7%; identical generated code or universal performance parity is not claimed. [All authentication samples and per-round comparisons](PREPARED.md).

## Updated NeoKit / Next / Astro core stats

Matched uncached small-page SSR, one server worker/process, one wrk thread and 32 connections; medians of three shuffled rounds. CPU at 200 requests/sec is a separate equal-load probe.

| Framework | Req/s | p99 ms | Peak sampled RSS MiB | CPU µs/request | CPU % at 200 req/s |
|---|---:|---:|---:|---:|---:|
| NeoKit prepared | 120,852 | 0.440 | 5.1 | 7.37 | 0.83 |
| Next | 973 | 50.610 | 380.2 | 1219.89 | 48.00 |
| Astro | 9,157 | 8.530 | 308.9 | 159.51 | 14.83 |

[Full core matrix](REPORT.md) includes single-connection latency, large/interactive pages, payload sizes, startup, versions and raw measurements.

## Async and streaming load

The fixed prepared feature application serves `/healthz` during continuous independent HTTP waits and real PostgreSQL/SCRAM streaming loaders. This fast route differs from SSR above.

| Background work | Clients | Fast req/s | Fast p99 ms | Peak sampled RSS MiB |
|---|---:|---:|---:|---:|
| idle | 0 | 105,900 | 0.490 | 4.7 |
| network-waits | 32 | 102,014 | 1.280 | 6.3 |
| database-and-network-streams | 16 | 93,366 | 5.220 | 6.3 |

All trials validate bodies/status and drain to zero task/capture/result counters. [Mixed-load CPU and raw trials](mixed/REPORT.md).

## Verification and scope

100 completed timing trials accompany 22 focused compiler tests and 2,385 JSON differential cases. Auth, mutation, 1/2/4-worker async PostgreSQL/network tests, ARM64 Linux prepared application checks, verified TLS/DNS waits, slow-reader backpressure, cancellation, capacity and graceful shutdown pass. Actual-browser SSR/streamed-widget/static-export checks pass. [Validation and captured logs](VALIDATION.md), [source/executable identities](identities.json).

Only this task’s builds and browser checks were kept out of the final timing run. Other host work remained active. Per-round shuffling reduces drift; there is no affinity/thermal isolation or formal confidence interval. CPU uses process-group time with 10 ms resolution; RSS is sampled every 250 ms. Node can use background threads, and Next includes RSC/bootstrap payload costs. Database/peer process resources are excluded from NeoKit mixed-load CPU/RSS. These local fixtures do not establish universal framework rankings or full feature parity. Linux checks establish functionality, not Linux performance.

Cooperative CPU scheduling, synchronous regular disk transfers, buffered PostgreSQL result sets and HTTP/1.1 connection-close streaming remain the supported profile.

![Verified final streamed sections and widget](browser-streaming.png)
