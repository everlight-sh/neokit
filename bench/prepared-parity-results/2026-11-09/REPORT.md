# Prepared library runtime parity

The final prepared dashboard is +1.0% versus monolithic on median paired throughput and +0.6% on paired CPU cost. It rendered 7,295 requests/s versus 7,337 for monolithic. These six paired rounds did not reproduce the earlier 1.6% throughput deficit consistently.

Neo now permits up to 32,768 lines of estimated forced expansion in pure dependency functions, with an 8,192-line cap in effectful functions and generated helpers. In the dashboard binary the two validated JSON indexing routines have zero call sites to the outlined member helper, down from six. The final policy keeps the existing object preparation pipeline. [Compiler policy and limits](../../../../axon/docs/prepared-library-inlining.md).

## Authenticated rendering

Six shuffled six-second rounds, 32 connections and one worker. The current prepared and monolithic controls use the same generated application source and release flags. The saved prepared executable is the previous 8,192-line build. Each trial validates owner filtering, escaped content and durable writes.

| Build | Requests/s | CPU µs/request | p99 ms | RSS MiB |
|---|---:|---:|---:|---:|
| Saved prepared | 7,501.5 | 130.98 | 8.325 | 3.56 |
| Current prepared | 7,295.1 | 130.95 | 16.075 | 3.56 |
| Monolithic | 7,337.1 | 129.96 | 11.305 | 3.45 |

Compared with the saved prepared binary, the median paired throughput change is -3.8% and the paired CPU cost change is +0.9%. Against monolithic, the ratio of aggregate throughput medians is -0.6%. Individual paired throughput changes versus monolithic range from -11.6% to +41.0% on the shared Mac. [All 18 trials](scoped-auth/prepared-results.json).

| Round | Prepared vs saved req/s | Prepared vs monolithic req/s | Prepared vs monolithic CPU/request |
|---:|---:|---:|---:|
| 1 | -6.9% | -9.1% | +3.9% |
| 2 | -6.2% | -11.6% | +5.5% |
| 3 | +2.7% | +3.7% | -1.5% |
| 4 | +0.2% | +41.0% | -11.3% |
| 5 | -10.6% | +7.5% | -2.1% |
| 6 | -1.4% | -1.7% | +2.8% |

The final cold build took 93.9 s, including 69.7 s of library preparation. A true warm rebuild took 20.2 s, reused the object and 1,364 prechecked obligations, and emitted the same binary. The monolithic build took 46.1 s. Cold preparation costs more when larger bodies survive inlining. These build times came from separate runs.

## Async rendering under load

Four alternating pairs ran six seconds of `/healthz` traffic at 32 connections with 16 clients continuously streaming real PostgreSQL and delayed HTTP results. The harness checked each streamed body and verified that task, capture and result counters returned to zero.

| Build | Fast requests/s | Fast p99 ms | CPU µs per fast request |
|---|---:|---:|---:|
| saved | 104,995 | 8.32 | 8.64 |
| scoped | 100,260 | 8.48 | 8.83 |

The scoped policy brought the mixed-load tail back close to the saved build. Fast-route throughput was -4.5% by aggregate medians and -4.5% on the median pair. That difference remains in this workload. CPU includes both fast responses and background streaming. [All mixed-load trials](scoped-async/).

## Realtime

Three shuffled six-second trials per count and build, at ten publications per second. Each recipient received every exact event in order. The separate serial test waits for every subscriber after each publication.

| Subscribers | Build | p50 ms | p99 ms | CPU % of one core | RSS MiB | Serial publishes/s |
|---:|---|---:|---:|---:|---:|---:|
| 100 | Saved | 1.31 | 26.98 | 0.33 | 4.75 | 593.2 |
| 100 | Current | 1.24 | 3.06 | 0.33 | 4.80 | 673.0 |
| 1,000 | Saved | 8.92 | 42.36 | 3.01 | 22.58 | 68.3 |
| 1,000 | Current | 7.56 | 14.44 | 2.85 | 22.62 | 74.8 |

The longer 100-subscriber test did not repeat the higher tail latency seen in the broader-cap experiment. The matched tests use one worker and the same local client on a shared Apple M1 Pro. [Raw SSE trials](scoped-sse/results.json).

## Verification

- The final browser build received live SSE updates and opened all 30 chart rows in the static export. [Browser record](validation/scoped-browser.json).
- 28 focused Neo compiler tests and the native cache suite passed, including corruption repair, dependency isolation, reuse and deterministic cold and warm builds.
- The current dashboard passed login/logout, escaping, authorization, concurrent durable writes and restart checks. [Dashboard test](validation/scoped-dashboard.log).
- The current async application passed PostgreSQL and delayed HTTP streaming, independent deadlines, disconnect cancellation and cleanup with one and two workers. [One worker](validation/scoped-async-1.json), [two workers](validation/scoped-async-2.json).
- Realtime passed authorization, replay/reset, heartbeat, slow readers, shutdown and worker churn on macOS and ARM64 Linux. [macOS](validation/scoped-realtime.json), [Linux](validation/scoped-linux-realtime.json).
- The earlier broad-cap build passed the native frame test with four backpressured 4 MiB responses during eviction, the feature smoke suite, the browser streamed counter and the static export. [Feature test](validation/features.log), [browser record](validation/browser.json).

The diagnostic runs are preserved. The global 32,768-line policy had comparable authenticated throughput but slower fast requests during active streams. The larger hint-import and single-helper probes also left a gap. The single-pass run was interrupted after seven trials. [Global-budget authentication](final-auth/prepared-results.json), [global-budget mixed load](async/), [hint probe](hint-probe/prepared-results.json), [member probe](member-inline/prepared-results.json), [interrupted single-pass run](one-pass/prepared-results.json).

[Source and executable identities](identities.json). The running dashboard uses the scoped build at [localhost:8113](http://localhost:8113).
