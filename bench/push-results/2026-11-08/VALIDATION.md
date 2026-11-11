# Publish-triggered SSE: validation and scope

The previous implementation already used browser EventSource/SSE, but its server
subscriptions checked channels every 250 ms. This follow-up removes that data
polling. Reusable changes live in Neo first: shared-channel notification,
native continuation watches and process-owned producer timers. NeoKit composes
those primitives. No Redis or server-side JavaScript engine is used.

Successful publications signal pre-fork worker socketpairs watched by
kqueue/epoll. Subscriptions run when their channel changes. Arming checks the
channel version to cover a racing publication; read contention waits for an
unlock notification. Retained history drains immediately in bounded batches,
and pending output resumes on socket write readiness. Timers only drive SSE
heartbeats/renewal and the independent synthetic data producer. Ordinary server
maintenance wakeups do not read subscriber data.

| Check | Result |
|---|---|
| Neo event fixture | Fork sharing, deduplication, value isolation, rollover/reset, UTF-8 framing; returns 42 |
| Neo producer timers | Independent interval publication, invalid interval rejection, parent ownership after fork, stop/cancellation; returns 42 on macOS and static ARM64 Linux |
| NeoKit compiler | 22 passing tests; [log](compiler-tests.log) |
| Browser modules | 27 passing tests across eight files, including subscription validation/replay, lifecycle cleanup and bounded chart/feed; [log](browser-unit-tests.log) |
| macOS HTTP/SSE | Zero idle subscriber callbacks/channel reads; unrelated channel does not trigger a callback; arbitrary-time external POST publications reach both subscribers; 16-event replay drains without a timer; heartbeat does not read the channel; auth, HEAD/methods, route precedence, async wait coexistence, cursor/reset/UTF-8/bounds, slow readers, cleanup and graceful shutdown; [results](protocol.json), [log](protocol-tests.log) |
| ARM64 Linux HTTP/SSE | Same final-source protocol suite passes, including multi-worker delivery after connection churn/slot compaction; [results](linux-protocol.json), [log](linux-tests.log) |
| Existing async streams | Root-to-stream handoff/rollback, isolated failure, eight backpressured 8 MiB streams with responsive fast requests, complete framing, 256-task bound, disconnect/deadline cleanup and graceful shutdown pass; [log](async-pressure-tests.log) |
| Existing recurring HTML streams | Shell-first delivery and grace/drain pass with one, two and four workers; [log](html-stream-tests.log) |
| Real database/network streaming | Current prepared feature application passes against disposable PostgreSQL 17 with SCRAM and an independently delayed HTTP peer: shell at 32.8 ms, fast network at 119 ms, database at 659 ms and slow network at 1,234 ms; isolated timeout at 284 ms, responsive ordinary requests, buffered async pages/APIs, disconnect cancellation, HEAD/method handling and zero idle counters; [results](async-database-network.json), [log](async-database-network.log) |
| Final showcase builds | Fresh SSR/timing, blog search/escaping/articles, computed chart/API data, 10,000-account pagination/filtering, CSV and activity pass; [log](showcase-tests.log) |
| Actual browser | Live update, unchanged paused cursor, immediate retained-event catch-up on resume, matching event/totals across tabs, 12-row feed bound, 390 px layout, static export with working chart disclosure and no live widget; no warning/error console entries; [record](browser-checks.json), [captures](screenshots/) |
| Native artifact audit | macOS executables have zero undefined symbols (the platform libSystem load command remains); Linux executables have no ELF interpreter, dynamic segment or undefined global/weak symbols; [audit](native-audit.json), [source identities](source-identities.json) |

[Protocol measurements](protocol.json) cover 24 publications at irregular
7/13/21/31 ms gaps. The macOS publish-request-to-both-subscribers delay was
1.54 ms median, 4.27 ms p95 and 4.46 ms maximum; Linux was 0.73/1.01/1.14 ms.
These include the independent HTTP peer's scheduling and the POST request.
Sixteen replayed events drained in 0.154 ms on macOS and 0.192 ms on Linux.
The idle and 15-second heartbeat checks recorded zero channel reads. These are
local observations, not delivery guarantees.

Performance trials separately measure unchanged page workloads against saved
polling-version binaries and 1/100/1,000 live subscribers alongside saturated
customer-directory SSR. All shuffled trials and executable identities are
retained in [results.json](results.json). Browser checks and compilation run
outside measured trials. The live load is the demo's 0.5 events/second, not a
high-frequency feed or 1,000 browser rendering sessions. RSS is sampled, and
the shared Mac has no thermal or CPU isolation.

Ordinary SSR medians changed by -3.8% for the SSR site, +1.5% for the blog and
-3.5% for the customer directory. These small drops are retained and are not
claimed to be eliminated. With 1,000 live subscribers, median customer-page
throughput is 11,418 requests/second at C1, event p99 is 15.1 ms, sampled peak
server RSS is 23.6 MiB and CPU is 78.3% of one core, including both SSR and event
delivery. Each subscriber received actual events during every measured trial.

The SaaS producer runs in worker zero every two seconds even with no
subscribers. It publishes explicit synthetic invoices/signups/plan changes;
pausing a browser does not pause the source. Historical analytics remain a
separate deterministic dataset. The browser keeps 60 chart samples and 12 feed
rows; the 10,000-account directory renders 25 rows. Its minified browser
bundle is unchanged at 5,639 bytes. Static export executes no startup hook and
opens no live subscription.

The backend shares bounded ephemeral history within one server family. It
does not share events between independently launched instances, persist events
across restart or automatically detect database changes. Application code calls
`web_publish` after a successful mutation or completed database/network task.
Database commit and event publication are separate operations. Delta consumers
must refetch an authoritative snapshot on replay reset. See the
[API profile](../../../docs/realtime.md) and
[Neo foundation documentation](../../../../axon/docs/events.md).

Reproduce the final protocol and browser-module checks from the NeoKit root:

```sh
./neokit build --app tests/realtime_fixture --out build/push/fixture
python3 tests/realtime.py --binary build/push/fixture/server --out bench/push-results/local/protocol.json
python3 -m unittest discover -s tests -p 'test_*.py'
bun test ./client
python3 bench/realtime.py --before-dir build/push-before --after-dir build/push --out bench/push-results/local
```

The benchmark expects preserved polling-version and current release builds of
`ssr-site`, `blog` and `saas` in those directories. It records exact executable
hashes, raw samples and method limits. Linux verification compiles the same
final fixture/timer source to ARM64 ELF and runs independent Python HTTP peers
in a disposable local container. No deployment, commit or push is performed.

The final verified showcase builds run at ports 8111–8113 under
`examples/run.py --no-build`; Meridian is at <http://localhost:8113>. The saved
preview builds and measured polling-version executables are retained under
`build/push-before`. The disposable PostgreSQL container was stopped and removed
after the integration test; existing Docker/Colima state is preserved.
