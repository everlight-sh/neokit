# Production SSE: NeoKit vs Next vs Astro

Installed versions: Next 16.1.1; Astro 7.4.7 with node adapter 11.1.11; Node v25.7.0. NeoKit prepared release executable. Host: Apple M1 Pro. One server worker/process, no Redis.

1 shuffled rounds per case. 10 external JSON publications/second for 1 seconds after warmup, each sent to every SSE subscriber. Payloads, order and counts are verified, as are access control, invalid input, replay and disconnect cleanup. Every trial must have zero dropped, duplicate or unexpected events.

| Subscribers | Framework | Delivery p50 ms | Delivery p99 ms | Last-subscriber p99 ms | Server RSS MiB | Active CPU % one core | Idle CPU % one core | Serial publish + all-delivered /s |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | neokit | 0.358 | 0.501 | 0.501 | 2.6 | 0.00 | 0.00 | 8,304.0 |
| 1 | next | 1.815 | 3.146 | 3.146 | 100.4 | 2.04 | 0.00 | 1,714.5 |
| 1 | astro | 1.144 | 1.880 | 1.880 | 83.2 | 1.02 | 0.00 | 3,298.4 |
| 100 | neokit | 1.043 | 1.985 | 2.052 | 4.5 | 0.00 | 0.00 | 710.8 |
| 100 | next | 3.045 | 4.626 | 4.699 | 126.3 | 6.13 | 0.00 | 420.2 |
| 100 | astro | 2.332 | 3.470 | 3.540 | 92.2 | 3.07 | 0.00 | 499.4 |
| 1,000 | neokit | 6.453 | 12.338 | 13.032 | 22.6 | 6.14 | 0.00 | 75.0 |
| 1,000 | next | 13.931 | 21.080 | 21.798 | 304.8 | 29.65 | 0.00 | 48.6 |
| 1,000 | astro | 10.920 | 18.985 | 19.733 | 184.3 | 14.29 | 0.00 | 63.5 |

## Method and limits

All three use publish-triggered SSE over local HTTP/1.1 with 128 retained events, a 4,096-byte JSON payload bound, a fixed cookie check and explicit subscriber cleanup. The Next/Astro application shares the same bounded in-process event hub and WHATWG ReadableStream implementation; their route/adapter overhead remains included. NeoKit uses its native shared-memory framework channel, with extra untimed fixture routes present. These fixtures measure local single-server realtime delivery, not complete application feature parity.

Delay starts on the independent client before the publish POST and ends when that client parses the received event. Delivery p50/p99 pool every subscriber/event observation. Last-subscriber p99 measures completion of the full fanout per publication. The client checks exact JSON content and consecutive event counters; a reset, missing event or closed stream fails the run. The HTTP publish response may arrive after event delivery.

Fixed-rate event throughput is an offered workload, not a saturation claim: 10 publications/second at 1,000 subscribers equals 10,000 event deliveries/second. The separate serial rate is a closed-loop publish-and-wait-for-every-subscriber loop, not maximum open-loop server capacity. Its Python parsing/fanout and loopback costs can dominate, especially at 1,000 subscribers. No application SSR runs concurrently in this matrix.

CPU is process-group CPU-time delta from ps (10 ms resolution); idle CPU over two seconds has limited precision. RSS is a 250 ms sample, after connections/warmup, excluding the load generator. One server process can use background threads; Node CPU may exceed 100%. All apps run in production mode with no deliberate GC. Framework memory includes warmed production routing/runtime costs.

Client and server share an unisolated Mac with uncontrolled thermals and background load. These short local trials measure neither internet/network distance, TLS/proxies, browser rendering, websockets, durable queues, multiple machines nor hosted/serverless limits. A smaller payload or different event rate/application can change rankings. Package versions identify locally installed builds, not the latest releases. [Raw samples and identities](results.json), [summary](summary.json), [fixture source](../../realtime_apps/hub.mjs).

Framework APIs: [Next Route Handlers](https://nextjs.org/docs/app/api-reference/file-conventions/route), [Astro endpoints](https://docs.astro.build/en/guides/endpoints/). The shared hub is a benchmark application, not a claim that either framework has NeoKit-compatible channels built in.
