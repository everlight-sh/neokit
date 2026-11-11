# NeoKit realtime measurements

Shuffled release trials, 4 seconds each after warmup, one native worker and one wrk thread on Apple M1 Pro. All rows are medians; trial counts are shown. No Redis or external realtime service.

| Unchanged SSR page, 32 connections | Trials per version | Before req/s | After req/s | Change | After peak RSS MiB | After CPU µs/req |
|---|---:|---:|---:|---:|---:|---:|
| ssr-site homepage | 3 | 113,103 | 112,723 | -0.3% | 4.5 | 7.91 |
| blog homepage | 3 | 98,200 | 98,444 | +0.2% | 3.5 | 9.38 |
| saas 10,000-account directory | 5 | 16,422 | 17,412 | +6.0% | 3.9 | 56.72 |

| Active SSE subscribers | Trials | Directory SSR req/s at C1 | SSR p99 ms | Event p50 ms | Event p99 ms | Peak RSS MiB | Total server CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3 | 11,764 | 0.341 | 1.0 | 1.1 | 4.2 | 74.6% |
| 100 | 3 | 11,471 | 3.340 | 2.6 | 3.3 | 5.6 | 73.2% |
| 1,000 | 3 | 10,589 | 3.020 | 9.0 | 17.9 | 24.1 | 72.0% |

Live trials carry actual server-published demo events every two seconds while rendering the 10,000-account directory on one additional HTTP connection. Every subscriber must receive events and stay connected; identical event IDs must carry identical payloads. Delivery delay uses the publisher wall-clock timestamp and receipt on the same Mac, and includes the 250 ms subscription poll interval plus load-generator scheduling. Clients resume a bounded history; these are synthetic events, not external financial transactions.

The first table compares unchanged page workloads against preserved pre-realtime binaries. The SaaS overview now contains a new live panel, so it is not used for a same-work comparison. CPU/RSS are server-process-group samples; live CPU includes both SSR and event delivery, so it is not attributed per HTML request. One worker supports 1,024 HTTP connections, including subscribers. The load generator is excluded from server CPU/RSS.

This is a shared Mac with no thermal/CPU isolation. Short trials and 250 ms RSS sampling have limited precision. Raw samples, outliers and binary identities are retained in [results.json](results.json). [Protocol verification](protocol.json), [browser checks](browser-checks.json), [validation and API scope](VALIDATION.md). Browser checks run separately from performance trials.

The initial full run and its 6% customer-page median drop are retained in [initial/results.json](initial/results.json). A framework dispatch change now tests a stream route match before walking its higher-priority exclusions. Customer and live cases were rerun; the unaffected site/blog samples were retained only after checking that both executable identities are unchanged. This is a local follow-up, not a claim that dispatch accounts for every initial timing difference.

At 1,000 subscribers, individual trial event p99 values range from 12.8 to 240.2 ms. The median does not hide the slower trial: the server polls at 250 ms, and these delivery times are not latency guarantees.
