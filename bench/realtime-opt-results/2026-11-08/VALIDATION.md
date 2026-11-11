# Realtime optimization regression checks

The reusable implementation lives in Neo before NeoKit adoption. No Redis,
external delivery process, server JavaScript runtime or C runtime was added.
Saved executables under `build/realtime-opt-before` are the comparison baseline.
The source snapshots and final native hashes are in
[source-identities.json](source-identities.json).

## Final implementation

Neo owns eight persistent scalar words per recurring HTTP callback, a 64-entry
process-private prepared-frame cache with exact keys and PID/generation tokens,
a direct nonblocking send path, and free-list/dense-vector callback admission
and removal. `std:event_stream` composes event history, SSE and prepared HTTP
frames. NeoKit uses these primitives for ordinary single-event delivery;
backlog/reset/large-event delivery retains bounded assembly and partial offsets.
The browser protocol and public subscription API remain unchanged.

An empty scheduler bypass avoids active executor stack/setup when there are no
callbacks, fibers or timers. It still refreshes HTTP ownership context, and all
three live-count guards match their respective scheduler drivers.

Wider Linux testing found an actual early-close bug in the new direct path:
checking a fresh clock after callback work could reject a wake time that was
valid at dispatch. Direct sends now use the same dispatch timestamp as legacy
callback packets. The overall response deadline remains enforced. A deliberate
5 ms callback with a 1 ms next wake is covered by the independent prepared peer.

## Checks

| Area | Result and evidence |
|---|---|
| Prepared state/frames on macOS | Pass: exact keys, mismatching content, retained owned payload after eviction, stale/forked tokens, 64 live entries, scalar slot reset, delayed wake, four concurrent backpressured 4 MiB bodies across eviction, ordinary requests, disconnect/shutdown; [log](validation-logs/neo-realtime-final-prepared-tests.log) |
| Legacy HTML streaming/shutdown | Pass with 1/2/4 workers and 150/2,000 ms grace, root handoff, rollback and failure; same native log |
| Async pressure | Pass: eight backpressured 8 MiB streams leave fast requests responsive, 256-task admission bound, slow-reader deadlines, graceful cancellation; same native log |
| Network/task waits | Pass verified TLS 1.3, HTTP, DNS, independent timers during file-lock contention, nested tasks, normal cleanup; same native log |
| Multiworker HTTP ownership | Pass 1,024 short-lived concurrent binary echoes with 2/4 workers, no retries; same native log |
| SSE protocol on macOS | Pass authorization/HEAD/methods, route precedence, async/SSE coexistence, exact shared delivery, replay/reset/UTF-8, heartbeat, zero idle data reads/callbacks, slow readers, cleanup/shutdown, two-worker churn; [results](protocol.json), [log](validation-logs/realtime-opt-final-protocol-tests.log) |
| ARM64 Linux | Pass static foundation result 42, same SSE suite and prepared peer with 1/2 workers including 4 MiB bodies; [results](linux-protocol.json), [log](validation-logs/realtime-opt-final-linux-tests.log) |
| Large SSE boundaries | Pass eight exact UTF-8 events at 3,900 and 7,900 embedded newlines; the latter exceeds a 48 KiB encoded callback budget. Reset/latest and cleanup pass; [normal](validation-logs/realtime-opt-final-boundaries-tests.log), [large](validation-logs/realtime-opt-final-large-boundaries-tests.log) |
| Real database/network rendering | Pass PostgreSQL SCRAM and independent delayed HTTP, shell-first output, out-of-order completion, isolated timeout, fast requests during waits, upstream disconnect cancellation and idle resource counts with 1/2 workers; [one](async-1.json), [two](async-2.json) |
| Ordinary framework behavior | Pass typed actions, privacy/cache invalidation, route/layout composition, multipart/binary 1 MiB, raw streaming, gzip/ETags/pipelining; [log](validation-logs/realtime-opt-final-features-tests.log) |
| Authenticated durable app | Pass 600,000-round native password login/logout, CSRF, escaping, cross-user isolation, 24 concurrent durable writes across two workers and restart; [log](validation-logs/realtime-opt-final-auth-tests.log) |
| Compiler/browser tests | Pass 22 NeoKit compiler tests and 27 browser-module tests across 8 files; [compiler](validation-logs/realtime-opt-final-compiler-tests.log), [browser](validation-logs/realtime-opt-final-client-tests.log) |
| Neo prepared-library integrity | Pass dependency reuse, deterministic cold/warm code, application checks/effects, imported-type/source invalidation, corrupt-object repair, no application code in cache; [log](validation-logs/neo-realtime-final-library-cache-tests.log). Neo return-alias/inlining/package tests: 27 pass; [log](validation-logs/neo-realtime-final-compiler-tests.log) |
| Independent consumer | Pass dependency isolation, native library reuse and static export, with the empty-scheduler guard before the dispatch-clock-only fix; [log](validation-logs/realtime-opt-final-consumer-tests.log) |
| Showcase pages | Pass fresh SSR/timing, blog search/articles/escaping, computed chart/API values, 10k pagination/filtering/last page, CSV and activity on the refreshed binaries; [log](validation-logs/realtime-opt-final-showcase-tests.log) |
| Actual browser | Final SSR pause held the cursor, resume replayed missed events and updated the graph, activity stayed capped at 12 rows. Static export opened 30 chart data rows and contained no live widget. No console errors; [record](browser-checks.json), [static screenshot](static.png). Public preview rechecked after replacement. |
| Native linking | Final macOS executables have zero undefined symbols; [audit](native-audit.json). The macOS binary retains the platform libSystem load command; this is not a claim that the loader is absent. Linux executables have no interpreter/dynamic section or undefined symbols; [audit](linux-link-audit.txt). |

## Measurement scope

Timed runs are serial, after builds and functional tests, with saved/current
executable hashes and all raw trials retained. The three SSR applications use
five shuffled before/after pairs at 32 connections and 6 seconds after warmup.
The authenticated app uses five shuffled rounds across the saved prepared,
current prepared and current monolithic binaries. Every auth trial validates
owner isolation/escaping and durable mutations before and after timing.

The SSE matrix uses three rounds each for saved/current NeoKit, production
Next.js and Astro, at 1/100/1,000 subscribers. The independent Python peer
requires every exact JSON event in order, without drop/reset/disconnect. Current
NeoKit must additionally match exact cache reuse counts. Ten publications per
second are used for fixed-rate CPU/latency; a separate closed serial loop measures
publication completion rate, not open-loop saturation capacity. Server CPU/RSS
exclude the load generator. No browser rendering, database or SSR is included
in that SSE comparison.

These are short measurements on a shared Mac without thermal/CPU isolation or
confidence intervals. CPU samples have 10 ms precision and RSS is sampled.
Loopback connections and Python parsing/scheduling affect delivery latency.
Zero idle CPU means below resolution. Passing this suite does not prove absence
of every possible regression or Next.js feature parity.

Earlier optimization trials are retained in
[2026-11-08-initial](../2026-11-08-initial/): CPU improved substantially while
1,000-subscriber latency did not. An initial simple-site throughput deficit
changed sign in a later diagnostic; neither phase is discarded. The final
report shows the broader paired checks and current results, including a longer
blog follow-up, nine mixed dashboard SSR/SSE trials, and a longer serial-window
check that does not reproduce the first matrix’s 7% serial deficit.

No source changes occur during the final timed matrices. Disposable PostgreSQL
and Linux test containers are removed after checks. No commit, push, deployment,
or unrelated Docker/image cleanup is performed.
