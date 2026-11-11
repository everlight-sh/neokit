# Matched realtime validation

The final matrix contains 27 passing trials: three shuffled runs of each
framework at 1, 100 and 1,000 subscribers. Each server starts in production
mode, admits connections in untimed batches of 32, publishes five verified
warmup events, waits two seconds for the idle resource sample, then accepts
40 external JSON publications over four seconds. Every publication must reach
every subscriber exactly once and in consecutive order. The data has a counter,
a UTF-8/injection-shaped text field and 128 padding characters.

All three implementations pass the same checks on every fresh process:

- An unauthenticated stream returns 403.
- Malformed JSON and a payload over 4,096 bytes return 400.
- Every subscriber receives every fixed-rate and closed-loop event with exact
  JSON values, consecutive counters and no duplicate/reset/disconnection.
- Closing the cohort, publishing two more events and reconnecting with the last
  cursor replays exactly those two events.
- Closing the final connection returns the reported subscriber count to zero.

The fixed-rate phase delivers 40,000 events per 1,000-subscriber trial. It
measures the same offered load for all three, not their maximum capacity.
The subsequent one-second serial phase publishes a new event only after every
subscriber has received the previous one. Its throughput includes Python
request/parsing/scheduling costs and does not establish open-loop capacity.

The final measured executable/source identities are in [results.json](results.json).
The [source snapshot](source-snapshot/) retains the measured harness, shared
JavaScript event hub, both frameworks' source/configuration, and the NeoKit
fixture. The normal prepared NeoKit executable is the same final native runtime
used for the [publish-triggered SSE protocol checks](../../push-results/2026-11-08/VALIDATION.md).
Its independently verified native-symbol/ELF and database/network streaming
checks are recorded there. No Neo runtime or dashboard code changed for this
comparison.

The Next/Astro fixtures share one benchmark application hub; actual framework
Route Handler/endpoint dispatch and native Node adapters remain in the timed
path. All three retain 128 events and use bounded output, a cookie equality
guard and explicit cleanup. This measures SSE transport with one channel and
one server worker/process. NeoKit's cross-worker sharing and framework channel
API are not duplicated as full features in the comparison application.

Production versions are the installed Next 16.1.1, Astro 7.4.7, @astrojs/node
11.1.11 and Node v25.7.0. JavaScript builds use `bun run build`; the deployed
comparison servers run on Node. No package installation, Redis, external broker,
browser rendering, TLS, database work or SSR load is part of this matrix.

Preflight caught a reentrant stream-pump callback in the shared JavaScript
benchmark fixture and an OS accept-backlog overflow from opening 1,000 sockets
simultaneously. Both were corrected before the final matrix. The staged
admission is outside timing and identical for all three servers. Earlier
preflight files/logs are retained in their named directories and are not treated
as timed production results. These were benchmark-fixture issues, not findings
against Next or Astro.

Results are medians of three short trials on a shared, unisolated Mac. Five
warmup publications do not prove complete JIT stabilization. CPU uses `ps`
process-group time with 10 ms precision, so reported zero idle CPU means below
measurement resolution. RSS is sampled every 250 ms. The shared Python client
can dominate fanout latency at 1,000 connections. This is a local comparison,
not a production latency guarantee or universal ranking.

Reproduce from the NeoKit root, with the existing local dependency directories
at `../makecademy/node_modules` (Next) and
`build/performance/astro-suite/node_modules` (Astro/node adapter):

```sh
python3 bench/realtime_parity.py --prepare --out bench/realtime-parity-results/local
```

The NeoKit fixture must already be built with the current release compiler:

```sh
./neokit build --app tests/realtime_fixture --out build/push/fixture
```

All benchmark processes are stopped after each trial. The existing local
Meridian preview remains running at <http://localhost:8113>.
