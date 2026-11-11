# NeoKit realtime measurements

3 shuffled release trials per case, 4 seconds each after warmup, one native worker and one wrk thread on Apple M1 Pro. All rows are medians. No Redis or external realtime service.

| Unchanged SSR page, 32 connections | Before req/s | After req/s | Change | After peak RSS MiB | After CPU µs/req |
|---|---:|---:|---:|---:|---:|
| ssr-site homepage | 113,103 | 112,723 | -0.3% | 4.5 | 7.91 |
| blog homepage | 98,200 | 98,444 | +0.2% | 3.5 | 9.38 |
| saas 10,000-account directory | 17,341 | 16,308 | -6.0% | 3.9 | 57.21 |

| Active SSE subscribers | Directory SSR req/s at C1 | SSR p99 ms | Event p50 ms | Event p99 ms | Peak RSS MiB | Total server CPU % |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 11,468 | 5.110 | 0.8 | 0.8 | 4.2 | 73.2% |
| 100 | 11,854 | 3.820 | 1.8 | 2.3 | 6.2 | 76.5% |
| 1,000 | 11,442 | 2.170 | 8.9 | 18.0 | 23.6 | 73.9% |

Live trials carry actual server-published demo events every two seconds while rendering the 10,000-account directory on one additional HTTP connection. Every subscriber must receive events and stay connected; identical event IDs must carry identical payloads. Delivery delay uses the publisher wall-clock timestamp and receipt on the same Mac, and includes the 250 ms subscription poll interval plus load-generator scheduling. Clients resume a bounded history; these are synthetic events, not external financial transactions.

The first table compares unchanged page workloads against preserved pre-realtime binaries. The SaaS overview now contains a new live panel, so it is not used for a same-work comparison. CPU/RSS are server-process-group samples; live CPU includes both SSR and event delivery, so it is not attributed per HTML request. One worker supports 1,024 HTTP connections, including subscribers. The load generator is excluded from server CPU/RSS.

This is a shared Mac with no thermal/CPU isolation. Short trials and 250 ms RSS sampling have limited precision. Raw samples, outliers and binary identities are retained in [results.json](results.json). [Protocol verification](protocol.json), [browser checks](browser-checks.json), [validation and API scope](VALIDATION.md). Browser checks run separately from performance trials.
