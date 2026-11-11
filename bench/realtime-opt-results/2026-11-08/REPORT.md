# NeoKit prepared realtime frames: performance and regressions

Neo owns the reusable fixes: retained scalar callback state, bounded prepared HTTP/SSE frames, direct nonblocking writes with owned partial-output copies, and a dense callback scheduler. NeoKit adopts these for publish-triggered SSE. No Redis is used. The final SaaS preview uses the same native executable measured here.

At 1,000 subscribers, current versus saved NeoKit changes server CPU by -57.6%, median end-to-end delivery by -21.8%, p99 by -25.5%, and serial publication completion by -7.0%. Lower CPU is distinct from a delivery-latency improvement; no order-of-magnitude speedup is claimed.

## Production SSE comparison

Apple M1 Pro, one server worker/process; locally installed Next 16.1.1, Astro 7.4.7, adapter 11.1.11, Node v25.7.0. Three shuffled trials per case; 10 external publications/s for 4 seconds, exact JSON delivered to every subscriber. Medians of trial statistics follow. CPU/RSS include only the server process group.

| Subscribers | Framework | Delivery p50 ms | Delivery p99 ms | Server RSS MiB | CPU % one core | Serial publish + all delivered /s |
|---:|---|---:|---:|---:|---:|---:|
| 1 | neokit-before | 0.381 | 1.171 | 2.72 | 0.25 | 7,945.7 |
| 1 | neokit | 0.409 | 1.441 | 2.91 | 0.25 | 9,127.7 |
| 1 | next | 2.760 | 8.165 | 109.08 | 4.78 | 1,822.8 |
| 1 | astro | 1.360 | 3.506 | 83.12 | 1.76 | 2,817.9 |
| 100 | neokit-before | 1.212 | 3.780 | 4.61 | 0.50 | 584.3 |
| 100 | neokit | 1.108 | 1.922 | 4.75 | 0.25 | 743.7 |
| 100 | next | 3.367 | 12.191 | 124.70 | 5.03 | 433.9 |
| 100 | astro | 3.214 | 18.334 | 94.34 | 4.27 | 569.4 |
| 1,000 | neokit-before | 9.728 | 20.718 | 22.64 | 6.54 | 88.0 |
| 1,000 | neokit | 7.608 | 15.432 | 22.72 | 2.77 | 81.9 |
| 1,000 | next | 15.902 | 23.696 | 305.98 | 18.12 | 48.5 |
| 1,000 | astro | 12.728 | 22.867 | 188.17 | 11.55 | 67.3 |

A longer native-only follow-up repeats three rounds with a five-second serial window: saved 82.95 versus current 82.50 complete publications/s (-0.5%). Fixed-rate CPU changes from 5.03% to 3.02% (-40.1%), p50 from 7.366 to 7.447 ms, and p99 from 14.519 to 14.258 ms. This does not reproduce the first matrix’s 7% serial deficit; it also does not establish a consistent delivery-latency improvement over the saved build. The repeatable finding is lower CPU. [All longer-window trials](serial-followup/results.json).

The prepared state/cache adds a small memory cost: the first matrix measures single-subscriber RSS 2.72 → 2.91 MiB. At 1,000 subscribers the longer follow-up measures 22.67 → 22.72 MiB. These measured increases remain visible; this is not a claim that every metric improves.

At 1,000 subscribers the fixed workload is 10,000 event deliveries/s. The serial column is a separate closed-loop publish-and-wait benchmark, not open-loop server capacity. The independent Python peer and loopback fanout affect latency. Next/Astro use the same bounded application hub and production endpoint/adapter paths. These are matched realtime fixtures, not complete framework feature parity. [Full method, raw samples and identities](parity/REPORT.md).

All current NeoKit trials require exactly one cache miss per measured publication and one hit per additional subscriber: 40 misses and 39,960 hits at 1,000 subscribers, with at most 64 live frames per worker. Encoding repeats after eviction. Short idle samples detect no periodic subscriber callbacks/channel reads; a 15-second heartbeat still works.

## Ordinary SSR and authentication

Five shuffled before/after pairs per page, 32 connections, 6 seconds after warmup. The SaaS workload queries 10,000 synthetic account records and renders a bounded 25-row directory. Both aggregate-median changes and within-round paired changes are shown, because host drift can change the aggregate ordering.

| Page | Saved req/s | Current req/s | Change of medians | Median paired change | Current RSS MiB | Current CPU µs/request |
|---|---:|---:|---:|---:|---:|---:|
| ssr-site | 142,454 | 153,049 | +7.4% | +7.9% | 4.34 | 6.23 |
| blog | 133,888 | 127,747 | -4.6% | +1.7% | 3.98 | 7.67 |
| saas | 17,819 | 17,930 | +0.6% | +0.5% | 4.33 | 55.59 |

The first blog matrix is mixed: three pairs improve and two decline; its aggregate median is below the saved build. That result is retained. A targeted six-pair follow-up uses 10-second trials, with both build orders: saved 110,440 req/s, current 120,049, change of medians +8.7%, median paired change +5.8% (individual pairs -8.8% to +22.1%). [First matrix](regression/results.json), [paired summaries](regression/summary.json), [longer follow-up](blog-followup/results.json).

Authenticated prepared rendering: saved 8,279, current 8,385, current monolithic 8,523 req/s. Current prepared changes by +1.3% versus the saved authenticated-fix prepared build and -1.6% versus current monolithic. The residual control difference is not hidden or called zero. Every trial checks owner-filtered/escaped output and durable mutations before and after timing. [All 15 trials and scope](prepared/PREPARED.md).

## Dashboard SSR with live subscribers

Final native SaaS executable, three shuffled trials per client count, four seconds after warmup. One additional connection renders the 10,000-account directory while synthetic application publications arrive every two seconds. Every subscriber must receive measured events, stay connected, and receive the same payload for a shared event ID.

| SSE subscribers | Directory SSR req/s at C1 | SSR p99 ms | Event p99 ms | RSS MiB | Total server CPU % |
|---:|---:|---:|---:|---:|---:|
| 1 | 13,461 | 0.102 | 0.85 | 4.66 | 79.3 |
| 100 | 13,525 | 0.100 | 2.39 | 6.62 | 79.8 |
| 1,000 | 12,221 | 0.114 | 13.38 | 24.41 | 82.3 |

CPU here includes both SSR and realtime. Event delay is measured from the demo publisher timestamp on the same Mac, including client scheduling. [Raw trials](mixed-live.json), [reproduction script](followup.py).

## Regression evidence and limits

[The validation record](VALIDATION.md) covers native macOS and static ARM64 Linux, prepared-frame eviction/backpressure, large SSE replay, legacy streaming, async PostgreSQL/network/TLS/DNS waits, file-lock tasks, shutdown deadlines, two/four-worker ownership, authentication and durable writes, compiler/browser-module tests, and actual SSR/static-export browser behavior. Wider Linux testing found and fixed an early-close regression when callback work crossed its next wake time; a deliberate delayed-wake peer now checks it. Final source/binary hashes and snapshots are recorded.

Earlier measurements remain in [2026-11-08-initial](../2026-11-08-initial/VALIDATION.md), including unfavorable latency/throughput trials and the simple-site follow-up. They are not overwritten by this final matrix. The final empty-scheduler bypass avoids active callback/fiber/timer setup for ordinary buffered requests; the active paths retain their cleanup/deadline behavior.

This is an unisolated shared Mac: CPU frequency, scheduler placement, background activity and thermals are uncontrolled. The initial SSR matrix has substantial host variation. There are no confidence intervals or universal no-regression guarantees. CPU has 10 ms precision; RSS is sampled. The per-worker frame cache bounds live entries, not total allocator RSS. Separate machines, durable messaging, WebSockets, proxies/TLS and internet latency are outside the realtime comparison. Installed versions identify tested local builds, not the latest versions. No runtime changes occurred during final timing.

Open the running example at [http://127.0.0.1:8113](http://127.0.0.1:8113). No commit, push or deployment was performed.
