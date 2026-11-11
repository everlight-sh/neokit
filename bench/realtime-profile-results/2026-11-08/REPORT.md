# Why the SSE delivery advantage is smaller than the SSR advantage

No runtime/framework optimization was made in this investigation. The original
[27-trial production comparison](../../realtime-parity-results/2026-11-08/REPORT.md)
and its measurements remain unchanged. This diagnostic identifies work to
remove and separates evidence from suspected causes.

## What the original comparison measures

All frameworks use one server worker/process and push the same small event to
1,000 TCP/SSE connections. The measured delay includes the publisher POST,
kernel scheduling, individual socket writes, receiver scheduling, HTTP/SSE
decoding and validation. The comparison application does much less computation
than an SSR page. Native compilation cannot remove the shared transport and
receiver work, so the SSR throughput advantage does not translate directly into
an equal SSE latency advantage.

NeoKit's median delivery advantage over Next decreases from 4.13× with one
subscriber to 2.10× with 1,000. The original equal-load server CPU results still
show a 3.58× advantage over Next and 2.05× over Astro at 1,000 subscribers;
sampled server memory is 13.06×/8.04× smaller respectively. CPU time and
end-to-end latency are different measurements.

## Concrete repeated work

| Current path | Evidence | Candidate improvement |
|---|---|---|
| JSON continuation state | `web_events_step` parses its capture, extracts five fields, then reconstructs JSON for the next callback on each subscriber update | Reusable typed/binary continuation state in Neo; retain the channel handle instead of looking its name up again |
| Repeated encoding/validation | Every subscriber calls `sse_encode` for the same event/ID. The encoder validates JSON, searches fields and builds frame bytes again | Validate once on publication and reuse immutable encoded frames, preserving bounded lifetime and slow-reader safety |
| Capture and output copies | `http_async_resume` builds a response packet; the executor copies its output into the socket queue and allocates/copies the following capture | Owned persistent state and fewer intermediate byte buffers |
| Fixed scheduler scans | Tick/timeout/cancel code scans up to 2,048 callback slots; readiness backends also walk connection interests | Ready/active indexes and deadline bookkeeping, after measuring the serialization/copy changes |

Source: [NeoKit continuation](../../../src/realtime.neo:17),
[Neo SSE encoder](../../../../axon/stdlib/sse.neo:36),
[Neo callback executor](../../../../axon/runtime/http_async.neo:61).
The Next/Astro comparison hub already creates one encoded event frame per
publication and shares it across subscribers. NeoKit currently repeats that
work for every recipient. These are real implementation differences; their
individual speedup potential has not yet been isolated.

Native sampling observed callback execution/socket sends, string search,
JSON quoting/indexing, allocation and copying. Disassembly maps sampled
`rt_async_tick+1844` to the instruction following the send syscall, and
`rt_http_kqueue_worker+1652` to the instruction following the kevent syscall.
The latter includes waiting and must not be interpreted as CPU time spent
computing. Inlining and coarse sampling prevent assigning precise percentages
to each source-level operation. [Native sample](native-busy-sample.txt).

## Client-side diagnostics

An additional run of the original Python peer, without Python function
instrumentation, delivered 40,000 exact events during its fixed-rate interval:

| Process | CPU time over the fixed interval | CPU as % of one core |
|---|---:|---:|
| NeoKit server | 0.260 s | 6.54% |
| Python peer | 0.673 s | 16.93% |

This establishes substantial client work; it does not quantify how much of
each event's wall-clock delay is caused by the client. This is one additional
diagnostic trial with native sampling, not a replacement for the original
three-round comparison. [Result](native-uninstrumented.json).

A separate cProfile run found work in asyncio stream reads/scheduling, frame
field construction and `json.loads`. It heavily perturbs the workload: the
instrumented median rose to 52.5 ms while server fixed-rate CPU remained around
4.5%. Those instrumented latencies are not performance results. The process-CPU
timer can also attribute work across asynchronous/callback boundaries; nested
cumulative times are not an additive cost breakdown.
[Client profile](client-profile.txt), [instrumented result](profile.json).

To test the receiver hypothesis, a second peer used Node's native HTTP framing
and exact raw payload/order comparisons, eliminating per-recipient JSON
decoding. Three shuffled trials per framework, unchanged servers, 1,000
subscribers and the same 40 publications produced:

| Server | Alternative peer delivery p50 ms | p99 ms | Peer CPU % one core |
|---|---:|---:|---:|
| NeoKit | 10.055 | 22.408 | 17.14% |
| Next | 16.289 | 38.773 | 12.49% |
| Astro | 14.309 | 30.416 | 14.38% |

All nine trials verified all 40,000 payloads per trial, consecutive counters,
and no duplicates or reset. The alternative peer waits for the entire cohort
after each publication, and changes HTTP client/runtime/scheduling behavior.
It was **not faster** than the original Python peer. Consequently, these
experiments do not establish that Python JSON parsing is the dominant limit,
or that changing the receiver alone will expose a much larger NeoKit lead.
All observations are retained in [fast-peer.json](fast-peer.json); despite the
diagnostic filename, it did not prove to be a faster peer.

An initial Bun invocation of this alternative peer timed out during setup;
no timed samples from it are included. The completed experiment uses the same
installed Node v25.7.0 as the comparison servers. No packages were installed.

## Order of further work

First measure reusable owned/binary continuation state and validated frame
reuse in Neo, then integrate those foundations into NeoKit. Next measure
buffer-copy reductions and active/ready scheduler indexes. Preserve the original
benchmark alongside a receiver whose throughput ceiling has been established;
repeat exact-event, backpressure, replay, cancellation, multi-worker and async
database/network checks after any runtime change. This is an optimization
priority list, not a claim of a measured gain or guaranteed multiplier.
