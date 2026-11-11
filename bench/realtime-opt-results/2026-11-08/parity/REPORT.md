# Production SSE: NeoKit vs Next vs Astro

Installed versions: Next 16.1.1; Astro 7.4.7 with node adapter 11.1.11; Node v25.7.0. NeoKit prepared release executable. Host: Apple M1 Pro. One server worker/process, no Redis.

3 shuffled rounds per case. 10 external JSON publications/second for 4 seconds after warmup, each sent to every SSE subscriber. Payloads, order and counts are verified, as are access control, invalid input, replay and disconnect cleanup. Every trial must have zero dropped, duplicate or unexpected events.

| Subscribers | Framework | Delivery p50 ms | Delivery p99 ms | Last-subscriber p99 ms | Server RSS MiB | Active CPU % one core | Idle CPU % one core | Serial publish + all-delivered /s |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | neokit-before | 0.381 | 1.171 | 1.171 | 2.7 | 0.25 | 0.00 | 7,945.7 |
| 1 | neokit | 0.409 | 1.441 | 1.441 | 2.9 | 0.25 | 0.00 | 9,127.7 |
| 1 | next | 2.760 | 8.165 | 8.165 | 109.1 | 4.78 | 0.00 | 1,822.8 |
| 1 | astro | 1.360 | 3.506 | 3.506 | 83.1 | 1.76 | 0.00 | 2,817.9 |
| 100 | neokit-before | 1.212 | 3.780 | 4.070 | 4.6 | 0.50 | 0.00 | 584.3 |
| 100 | neokit | 1.108 | 1.922 | 2.061 | 4.8 | 0.25 | 0.00 | 743.7 |
| 100 | next | 3.367 | 12.191 | 12.477 | 124.7 | 5.03 | 0.00 | 433.9 |
| 100 | astro | 3.214 | 18.334 | 18.988 | 94.3 | 4.27 | 0.00 | 569.4 |
| 1,000 | neokit-before | 9.728 | 20.718 | 23.503 | 22.6 | 6.54 | 0.00 | 88.0 |
| 1,000 | neokit | 7.608 | 15.432 | 20.123 | 22.7 | 2.77 | 0.00 | 81.9 |
| 1,000 | next | 15.902 | 23.696 | 26.328 | 306.0 | 18.12 | 0.00 | 48.5 |
| 1,000 | astro | 12.728 | 22.867 | 25.517 | 188.2 | 11.55 | 0.00 | 67.3 |

## Method and limits

All three use publish-triggered SSE over local HTTP/1.1 with 128 retained events, a 4,096-byte JSON payload bound, a fixed cookie check and explicit subscriber cleanup. The Next/Astro application shares the same bounded in-process event hub and WHATWG ReadableStream implementation; their route/adapter overhead remains included. NeoKit uses its native shared-memory framework channel, with extra untimed fixture routes present. These fixtures measure local single-server realtime delivery, not complete application feature parity.

Delay starts on the independent client before the publish POST and ends when that client parses the received event. Delivery p50/p99 pool every subscriber/event observation. Last-subscriber p99 measures completion of the full fanout per publication. The client checks exact JSON content and consecutive event counters; a reset, missing event or closed stream fails the run. The HTTP publish response may arrive after event delivery.

Fixed-rate event throughput is an offered workload, not a saturation claim: 10 publications/second at 1,000 subscribers equals 10,000 event deliveries/second. The separate serial rate is a closed-loop publish-and-wait-for-every-subscriber loop, not maximum open-loop server capacity. Its Python parsing/fanout and loopback costs can dominate, especially at 1,000 subscribers. No application SSR runs concurrently in this matrix.

CPU is process-group CPU-time delta from ps (10 ms resolution); idle CPU over two seconds has limited precision. RSS is a 250 ms sample, after connections/warmup, excluding the load generator. One server process can use background threads; Node CPU may exceed 100%. All apps run in production mode with no deliberate GC. Framework memory includes warmed production routing/runtime costs.

Client and server share an unisolated Mac with uncontrolled thermals and background load. These short local trials measure neither internet/network distance, TLS/proxies, browser rendering, websockets, durable queues, multiple machines nor hosted/serverless limits. A smaller payload or different event rate/application can change rankings. Package versions identify locally installed builds, not the latest releases. [Raw samples and identities](results.json), [summary](summary.json), [fixture source](../../../realtime_apps/hub.mjs).

Framework APIs: [Next Route Handlers](https://nextjs.org/docs/app/api-reference/file-conventions/route), [Astro endpoints](https://docs.astro.build/en/guides/endpoints/). The shared hub is a benchmark application, not a claim that either framework has NeoKit-compatible channels built in.
