# Realtime validation and scope

Validated locally on 2026-11-08. Neo/shared foundations were implemented before
the NeoKit API and Meridian integration. No Redis, hosted event service, new
runtime dependency or server-side JavaScript engine is used.

| Check | Result |
|---|---|
| Neo foundations | Named channels, fork sharing, producer-key deduplication, retained-value isolation, rollover/reset and UTF-8 SSE framing; standalone fixture returns 42 |
| NeoKit compiler | 22 tests pass, including configured channel/history/payload limits and rejection of oversized shared storage; [log](compiler-tests.log) |
| Browser modules | 27 tests pass, including replay reset, deduplication, stale callbacks, pause/resume, visibility cleanup, widget subscription disposal on navigation and failed mount, cross-origin rejection, invalid/oversized payloads, bounded 60-point chart/12-row feed and safe text rendering; [log](browser-unit-tests.log) |
| HTTP/SSE peer | Auth hooks, HEAD/methods, static route precedence over a dynamic stream, async-loader/SSE coexistence, identical subscriber events, cursor replay, old/future/previous-epoch reset, heartbeat, bounds, slow readers, zero idle resources and shutdown; [results](protocol.json), [log](protocol-tests.log) |
| Multi-worker delivery | Same epoch, event ID and payload across forked workers; additionally observed across four connections on a ten-worker demo server |
| Public package consumer | A separately locked application imports `pkg:neokit/realtime`, configures channels and publishes through the public API; returns 42; [log](consumer-tests.log) |
| Existing async rendering | Root-to-stream handoff, rollback, failure before headers, eight backpressured 8 MiB streams, complete framing, exact 256-task bound, cancellation/deadline cleanup and shutdown pass; [log](async-pressure-tests.log) |
| Existing recurring HTML streams | Shell-first delivery, responsive ordinary requests, and grace expiry/drain pass with 1/2/4 workers; [log](html-stream-tests.log) |
| Existing NeoKit features | Typed actions/validation, cache privacy/invalidation, dynamic/catch-all routing, layouts, multipart binary upload, raw streaming, gzip/ETag and pipeline ordering pass; [log](feature-tests.log) |
| Showcase regression checks | Fresh SSR, measured timing, blog search/escaping/articles, computed dashboard API/charts, 10,000-account bounded pagination/filtering and CSV pass on the final preview binaries; [log](showcase-tests.log) |
| Actual browser | Live metrics/rolling chart/feed, pause/resume, matching tabs, continued delivery through connection renewal, 390 px layout and static export checked; [record](browser-checks.json), [captures](screenshots/) |
| ARM64 Linux core | Static native event fixture returns 42 in a nonroot/read-only/capability-dropped container. SSE auth/framing/replay/reset/heartbeat/backpressure/cleanup/shutdown and two-worker sharing pass; [results](linux-protocol.json). This Linux HTTP artifact precedes the pure dispatch-order follow-up; final precedence and async/SSE coexistence checks ran on macOS |
| Artifact audit | macOS executables have no undefined symbols; Linux foundation/protocol executables have no ELF interpreter, dynamic segment or undefined global/weak symbols; [audit](native-audit.json) |

The original full performance run and its customer-page median regression are
retained under `initial/`. The compiler now checks the stream's own route before
checking higher-priority exclusions. Five balanced trials per customer-page
version followed that change. Site/blog measurements were reused only after
checking unchanged executable identities. Three new live trials per subscriber
count require **every** subscriber to receive an event inside the measurement
window and remain connected. Same event IDs must carry identical data.

The live load is the demo's 0.5 events/second, while one extra client saturates
SSR of the 10,000-account directory. It does not measure a high-frequency market
feed, browser rendering on 1,000 devices, network distance or durable delivery.
The native backend polls at 250 ms and emits at most four events per poll. One
1,000-client trial had event p99 around 240 ms; all samples remain visible.
These short shared-Mac trials support local comparisons, not production SLAs.

The SaaS browser bundle is 5,639 bytes uncompressed/minified, including its small
entry stub, timing badge, chart exploration, validators and subscription helper.
The live producer generates explicit synthetic events only while subscribed.
Absolute live counters are separate from seeded historical analytics; the
customer table remains limited to 25 rendered rows. The live chart/feed stay
bounded at 60 samples/12 rows. Static exports open no event subscription.

Channels share ephemeral bounded history across one server's workers. Separate
server instances do not share publications, and a full restart loses history.
Mutations and publication are separate operations. Generic delta consumers must
refetch an authoritative snapshot on replay reset. See the [API profile](../../../docs/realtime.md)
for authorization, contention, connection and storage limits.

Reproduce from the NeoKit root:

```sh
python3 examples/run.py
env PYTHONPATH=. python3 tests/realtime.py --out bench/realtime-results/local/protocol.json
env PYTHONPATH=. python3 tests/showcase_smoke.py
python3 -m unittest discover -s tests -p 'test_*.py'
bun test ./client
python3 bench/realtime.py --out bench/realtime-results/local
```

The protocol test expects its fixture built with
`./neokit build --app tests/realtime_fixture --out build/realtime/fixture`.
The benchmark uses preserved `build/realtime-before` binaries and current
`build/realtime` release builds; both phases' identities are recorded. The final
follow-up used `--rounds 5 --live-rounds 3 --reuse-unaffected
bench/realtime-results/2026-11-08/initial/results.json`. The helper verifies
unaffected binary identities before reusing any samples.

Final user-facing native previews run at ports 8111–8113, supervised by
`examples/run.py --no-build`. Build caches were evicted when disk space became
tight; executable baselines, source and reports were retained. No deployment,
git commit or push was performed.
