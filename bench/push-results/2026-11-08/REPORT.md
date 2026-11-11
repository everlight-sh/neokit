# NeoKit publish-triggered SSE measurements

Shuffled release trials, 4 seconds each after warmup, one native worker and one wrk thread on Apple M1 Pro. All rows are medians; trial counts are shown. No Redis or external realtime service.

| Unchanged SSR page, 32 connections | Trials per version | Before req/s | After req/s | Change | After peak RSS MiB | After CPU µs/req |
|---|---:|---:|---:|---:|---:|---:|
| ssr-site homepage | 3 | 117,464 | 113,042 | -3.8% | 4.4 | 8.13 |
| blog homepage | 3 | 104,641 | 106,170 | +1.5% | 3.6 | 9.24 |
| saas 10,000-account directory | 3 | 17,804 | 17,179 | -3.5% | 4.3 | 57.58 |

| Active SSE subscribers | Trials | Directory SSR req/s at C1 | SSR p99 ms | Event p50 ms | Event p99 ms | Peak RSS MiB | Total server CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3 | 12,376 | 0.319 | 1.0 | 1.0 | 3.9 | 79.3% |
| 100 | 3 | 12,652 | 0.193 | 2.0 | 2.6 | 6.0 | 82.3% |
| 1,000 | 3 | 11,418 | 0.207 | 8.7 | 15.1 | 23.6 | 78.3% |

Live trials carry actual server-published demo events every two seconds while rendering the 10,000-account directory on one additional HTTP connection. Every subscriber must receive events inside the measured interval and stay connected; identical event IDs must carry identical payloads. Delivery delay uses the publisher wall-clock timestamp and receipt on the same Mac, including load-generator scheduling. Publications wake workers through their kernel-watched IPC sockets; subscribers do not periodically poll channel data. A separate process-owned timer generates the synthetic data every two seconds. Clients resume a bounded history; these are synthetic events, not external financial transactions.

The first table compares unchanged page workloads against preserved binaries from the previous polling implementation. The SaaS comparison uses the same 10,000-account directory, with 25 rendered rows. CPU/RSS are server-process-group samples; live CPU includes both SSR and event delivery, so it is not attributed per HTML request. One worker supports 1,024 HTTP connections, including subscribers. The load generator is excluded from server CPU/RSS.

This is a shared Mac with no thermal/CPU isolation. Short trials and 250 ms RSS sampling have limited precision. Raw samples, outliers and binary identities are retained in [results.json](results.json). [Protocol verification](protocol.json), [browser checks](browser-checks.json), [validation and API scope](VALIDATION.md). Browser checks run separately from performance trials.

At 1,000 subscribers, individual trial event p99 values range from 14.6 to 16.0 ms. All trials are retained; these local delivery times are not latency guarantees.

An independent protocol test publishes at irregular intervals, separate from the demo timer: macOS POST-to-both-subscribers delay was 1.54 ms median, 4.27 ms p95 and 4.46 ms maximum. Idle subscription checks recorded 0 channel reads and 0 callbacks. A separate heartbeat assertion verifies it does not read the channel. See [validation](VALIDATION.md) for Linux results, replay timing and regression checks.
