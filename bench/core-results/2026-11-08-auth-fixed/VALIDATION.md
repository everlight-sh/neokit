# Verification of the authenticated rendering fix

All application/compiler/runtime implementations remain native Neo. The
runtime changes in this fix are reusable compiler/library optimizations; the
NeoKit application and its authentication rules are unchanged.

| Check | Result |
|---|---|
| Focused Neo compiler tests | 22 pass: return aliases, inline budgets, caller independence, LLVM backend behavior, metadata placement and separate object/application budgets ([log](compiler-tests.log)) |
| Prepared-library cache integration | Pass: cold/warm binary identity, effects/checks, dependency/type invalidation, corrupt hint/object repair, return-alias facts and async callbacks ([log](native-library-cache.log)) |
| JSON accessors | 745 independent differential cases pass, including long escape/plain runs, controls, UTF-8, surrogate pairs, escaped/duplicate keys, wide objects, alias ownership and invalid inputs ([log](json-accessors.log)) |
| JSON serialization | 428 independent serialization/update/escaping/alias/depth cases pass with prepared libraries ([log](json-serialization.log)) |
| JSON field decoder | 1,212 differential cases pass: prepared field decoders, arrays, escaped/duplicate/Unicode keys, integer limits and invalid inputs ([log](json-fields.log)) |
| Authenticated dashboard | Native 600,000-round password login, logout, CSRF, escaping, cross-user authorization, 24 concurrent durable writes across two workers and restart persistence pass ([log](dashboard-tests.log)) |
| Async PostgreSQL/network application | Real SCRAM PostgreSQL queries and independent delayed HTTP peers pass at 1/2/4 workers: shell-first rendering, independent sections, isolated timeout, silent-upstream disconnect cancellation, HEAD/method dispatch, buffered 504 and zero idle task/capture/result counters ([log](async-tests.log)) |
| Existing NeoKit feature suite | Typed routes/layouts, action validation, 1 MiB binary uploads, cache invalidation/privacy, gzip/assets, raw streaming and pipeline ordering pass ([log](async-tests.log)) |
| ARM64 Linux prepared application | Same real PostgreSQL/network suite passes in a nonroot/read-only/capability-dropped container ([log](linux-tests.log)) |
| Native prepared-library async pressure | Header handoff, queued-child timeout, partial admission rollback, root failure, eight backpressured 8 MiB streams, full body/framing, exact 256-task limit, cancellation/deadline cleanup and SIGTERM grace cancellation pass |
| Native prepared-library protocol waits | Independently delayed verified TLS 1.3, HTTP and DNS overlap; await deadlines and scoped handle cleanup pass |
| Actual in-app browser | Streamed sections resolve independently, inserted counter 0→2, fresh static-export counter 0→2→0, no browser warnings/errors ([DOM/timing observations](browser-checks.json), [screenshot](browser-streaming.png)) |
| Native artifact audit | macOS prepared dashboard/features/protocol executables have no undefined symbols. ARM64 Linux prepared application has no interpreter, dynamic dependencies or undefined symbols |

The async pressure test kept 30 fast requests completing in about 7 ms while
eight readers were backpressured on 8 MiB streams. This is a functional stall
check, separate from the timed throughput matrix.

PostgreSQL is an independent disposable test peer using the official PostgreSQL
17 Alpine image. Python/OpenSSL/Docker are test tools, not implementations inside
Neo. No Redis is used. Only this task's database container is removed at cleanup;
pre-existing Colima state and unrelated host work are preserved.

Source and executable hashes are recorded in [identities.json](identities.json).
The broader async protocol limitations still apply: cooperative CPU scheduling,
synchronous regular disk transfers, buffered PostgreSQL results and HTTP/1.1
connection-close streaming. See [async usage and limits](../../../docs/async-rendering.md).
