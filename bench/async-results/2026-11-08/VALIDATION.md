# Async rendering verification

This records the commands and observed outcomes for the tested workspace; it is
not a claim that every Neo/NeoKit test or all Next.js features are covered.
Source and executable hashes are in [identities.json](identities.json).

| Check | Observed outcome |
|---|---|
| `bun test ./client` | 24 tests pass; out-of-order/incomplete/stale fragments, rejected executable fragments, late module arrival, buffered navigation fallback, widget lifecycle and existing list/state/navigation tests |
| `python3 -m unittest discover -s tests -p 'test_*.py'` (NeoKit) | 21 tests pass, including typed/bounded async configuration |
| `python3 -m unittest tests.test_return_aliases tests.test_inline_budget tests.test_inline_cost` (Neo) | 18 tests pass |
| `python3 tests/native_library_cache.py` (Neo) | Pass: cold/warm deterministic output, async callback in a prepared library, dependency/type invalidation, corrupt-object repair, application checks/effects and no application bodies in cache; [captured log](native-library-cache.log) |
| `python3 tests/async_tasks.py` (Neo) | Pass on macOS and ARM64 Linux: nested futures, fast timer progresses while another task waits on an independently held file lock, scoped cleanup |
| `python3 tests/async_protocols.py` (Neo) | Pass on macOS and ARM64 Linux: independently delayed HTTP, verified TLS 1.3 and DNS overlap; await timeout/cancellation; zero live handles |
| `python3 tests/async_pressure.py` (Neo) | Pass on macOS and ARM64 Linux: async-root-to-stream header handoff, queued-child timeout, partial-admission rollback, root failure, eight backpressured 8 MiB streams, complete 8 MiB body, 256-task admission limit, disconnect/deadline cleanup, SIGTERM grace cancellation |
| `python3 tests/http_async.py --binary build/http-async-check` (Neo) | Legacy continuations pass at 1/2/4 workers with both 150 ms and 2000 ms shutdown grace |
| `python3 tests/async_rendering.py --binary build/async-prepared/features/server --pg-port 32768 --workers N` | Pass with N=1,2,4 on macOS: real SCRAM PostgreSQL, HTTP prelude before stream start, independent query/network completions, escaped UTF-8, isolated boundary timeout, silent-upstream disconnects, HEAD/method handling, 504 before headers and zero idle counters; [worker 1](http-workers-1.json), [worker 2](http-workers-2.json), [worker 4](http-workers-4.json) |
| Same async HTTP integration against cross-compiled ARM64 Linux application | Pass against PostgreSQL 17 in an isolated nonroot/read-only/capability-dropped test container; application uses the same native runtime; [final generated-app run](linux-http-final.log) |
| `env PYTHONPATH=. python3 tests/features_smoke.py --out build/async-prepared/features` | Pass: existing routes/layouts, methods, binary 1 MiB uploads, typed actions, validation, cache invalidation/privacy, metadata, gzip/assets, streaming flush and pipeline ordering |
| `env PYTHONPATH=. python3 tests/dashboard_smoke.py --binary build/async-prepared/dashboard/server` | Pass: password login/logout, CSRF, escaping, cross-user authorization, 24 concurrent durable writes over two workers and restart persistence |
| Actual in-app browser: release prepared `/async` and freshly exported `/interactive/` | Streamed counter 0→2; static counter 0→2→0; three successful boundaries and one isolated error; no console warnings/errors; [final DOM observations](browser-final.json), [screenshot](browser-streaming.png) |
| ELF and symbol audit | ARM64 Linux task/protocol/pressure/application binaries and x86-64 task binary have no ELF interpreter, dynamic dependencies or undefined symbols; macOS task/protocol/prepared application binaries have no undefined symbols |
| x86-64 Linux task fixture under emulation | Nested tasks and timers return expected 42; this is functional emulation, not native x86 performance or a full x86 application/protocol suite |

Independent test peers use Python/OpenSSL and official PostgreSQL 17 Alpine.
They are not protocol implementations inside Neo. The test PostgreSQL container
has no persistent volume and is removed after verification. Existing Colima
state is preserved. No Redis service is used.

The final browser-only patch was followed by all 24 client tests, a fresh static
export, another actual-browser SSR/export check and another real-PostgreSQL HTTP
integration on the final macOS prepared binary ([result](http-final.json)). Native
executor sources did not change during those browser fixes.
