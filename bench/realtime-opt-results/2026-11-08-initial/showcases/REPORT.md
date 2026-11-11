# NeoKit publish-triggered SSE measurements

Shuffled release trials, 4 seconds each after warmup, one native worker and one wrk thread on Apple M1 Pro. All rows are medians; trial counts are shown. No Redis or external realtime service.

| Unchanged SSR page, 32 connections | Trials per version | Before req/s | After req/s | Change | After peak RSS MiB | After CPU µs/req |
|---|---:|---:|---:|---:|---:|---:|
| ssr-site homepage | 3 | 169,150 | 162,335 | -4.0% | 4.1 | 5.89 |
| blog homepage | 3 | 145,274 | 146,798 | +1.0% | 3.7 | 6.69 |
| saas 10,000-account directory | 3 | 18,101 | 18,090 | -0.1% | 4.3 | 55.15 |

| Active SSE subscribers | Trials | Directory SSR req/s at C1 | SSR p99 ms | Event p50 ms | Event p99 ms | Peak RSS MiB | Total server CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3 | 13,618 | 0.102 | 0.5 | 0.5 | 4.6 | 79.9% |
| 100 | 3 | 13,536 | 0.102 | 1.6 | 2.1 | 6.6 | 79.8% |
| 1,000 | 3 | 12,337 | 0.113 | 9.0 | 15.1 | 24.4 | 82.3% |

Live trials carry actual server-published demo events every two seconds while rendering the 10,000-account directory on one additional HTTP connection. Every subscriber must receive events inside the measured interval and stay connected; identical event IDs must carry identical payloads. Delivery delay uses the publisher wall-clock timestamp and receipt on the same Mac, including load-generator scheduling. Publications wake workers through their kernel-watched IPC sockets; subscribers do not periodically poll channel data. A separate process-owned timer generates the synthetic data every two seconds. Clients resume a bounded history; these are synthetic events, not external financial transactions.

The first table compares unchanged page workloads against preserved binaries from the publish-triggered implementation before frame/state optimization. The SaaS comparison uses the same 10,000-account directory, with 25 rendered rows. CPU/RSS are server-process-group samples; live CPU includes both SSR and event delivery, so it is not attributed per HTML request. One worker supports 1,024 HTTP connections, including subscribers. The load generator is excluded from server CPU/RSS.

This is a shared Mac with no thermal/CPU isolation. Short trials and 250 ms RSS sampling have limited precision. Raw samples, outliers and binary identities are retained in [results.json](results.json). [Protocol verification](protocol.json), [browser checks](browser-checks.json), [validation and API scope](VALIDATION.md). Browser checks run separately from performance trials.

At 1,000 subscribers, individual trial event p99 values range from 12.9 to 17.0 ms. All trials are retained; these local delivery times are not latency guarantees.
