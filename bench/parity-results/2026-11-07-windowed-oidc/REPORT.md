# NeoKit functionality expansion: local production measurements

Host: macOS-27.2-arm64-arm-64bit-Mach-O, Apple M1 Pro. Next 16.1.1, Node v25.7.0. One server worker/process, one wrk thread, identity encoding; 3 shuffled rounds of 2 seconds after a one-second warmup, with a 10-second client timeout.

NeoKit uses release/O3 monolithic builds (`NEOKIT_LIBRARY_CACHE=off`). Next uses its production webpack build with one build worker. The saved pre-expansion baseline is identified by binary hash; these figures compare the complete before/after binaries, not an isolated estimate of each new feature’s cost.

| Workload at 32 connections | NeoKit req/s | Next req/s | Throughput ratio | NeoKit vs saved baseline |
|---|---:|---:|---:|---:|
| small-ssr | 102,613.0 | 831.6 | 123.4× | -2.0% |
| large-ssr | 20,948.7 | 15.9 | 1320.0× | +16.1% |
| interactive-ssr | 93,425.2 | 377.7 | 247.3× | -1.1% |
| authenticated-db-ssr | 7,209.5 | 131.7 | 54.8× | new capability |
| durable-mutation | 83.4 | 69.4 | 1.2× | new capability |

Catalog throughput changes range from -2.0% to +16.1% against the saved baseline at 32 connections. Fully flushed mutations are storage-bound and show a much smaller advantage than SSR. See median and p99 latency separately; no universal latency improvement is implied. Next is the locally installed 16.1.1 production build, not an assertion about the latest release. These are short local trials without formal confidence intervals. Host load and disk headroom are recorded in results.json; storage and background activity make write timings especially provisional.

| Workload | Connections | Framework | Median req/s | Median p50 ms | Median p99 ms | Median RSS MiB |
|---|---:|---|---:|---:|---:|---:|
| small-ssr | 1 | baseline-neokit | 32770.5 | 0.025 | 0.086 | 2.2 |
| small-ssr | 1 | neokit | 34004.0 | 0.025 | 0.062 | 2.3 |
| small-ssr | 1 | next | 549.2 | 1.710 | 4.860 | 306.5 |
| small-ssr | 32 | baseline-neokit | 104757.9 | 0.196 | 0.477 | 2.8 |
| small-ssr | 32 | neokit | 102613.0 | 0.200 | 0.492 | 2.9 |
| small-ssr | 32 | next | 831.6 | 36.790 | 116.000 | 334.5 |
| large-ssr | 1 | baseline-neokit | 12298.0 | 0.074 | 0.131 | 2.2 |
| large-ssr | 1 | neokit | 12277.8 | 0.074 | 0.129 | 2.3 |
| large-ssr | 1 | next | 32.7 | 29.810 | 43.330 | 300.1 |
| large-ssr | 32 | baseline-neokit | 18043.8 | 1.550 | 6.550 | 2.8 |
| large-ssr | 32 | neokit | 20948.7 | 1.490 | 3.050 | 2.9 |
| large-ssr | 32 | next | 15.9 | 524.630 | 1920.000 | 363.2 |
| interactive-ssr | 1 | baseline-neokit | 29559.2 | 0.027 | 0.106 | 2.2 |
| interactive-ssr | 1 | neokit | 29607.8 | 0.028 | 0.081 | 2.3 |
| interactive-ssr | 1 | next | 313.4 | 3.080 | 5.030 | 306.9 |
| interactive-ssr | 32 | baseline-neokit | 94457.4 | 0.282 | 0.666 | 3.1 |
| interactive-ssr | 32 | neokit | 93425.2 | 0.286 | 0.695 | 3.2 |
| interactive-ssr | 32 | next | 377.7 | 79.270 | 342.140 | 339.2 |
| authenticated-db-ssr | 1 | neokit | 5796.3 | 0.164 | 0.262 | 2.8 |
| authenticated-db-ssr | 1 | next | 136.9 | 7.140 | 10.650 | 255.2 |
| authenticated-db-ssr | 32 | neokit | 7209.5 | 4.290 | 8.670 | 3.4 |
| authenticated-db-ssr | 32 | next | 131.7 | 207.290 | 1070.000 | 316.4 |
| durable-mutation | 1 | neokit | 99.7 | 9.810 | 20.400 | 2.8 |
| durable-mutation | 1 | next | 88.4 | 10.940 | 21.810 | 128.4 |
| durable-mutation | 32 | neokit | 83.4 | 325.120 | 653.210 | 3.4 |
| durable-mutation | 32 | next | 69.4 | 153.510 | 1680.000 | 134.5 |

| Representative decoded response | NeoKit bytes | Next bytes |
|---|---:|---:|
| small-ssr | 967 | 6,786 |
| large-ssr | 104,965 | 410,432 |
| interactive-ssr | 6,051 | 26,082 |
| authenticated-db-ssr | 43,226 | 122,645 |

The fixtures match rendered table/task data and behavior under test, not wire bytes. Next includes its App Router/RSC bootstrap and serialized component data; NeoKit sends HTML with optional widget modules. The throughput comparison includes this architectural payload difference. External browser asset loading is not part of wrk throughput. [Actual-browser functional checks](../../browser-results/2026-11-07-windowed/REPORT.md) are recorded separately. A preliminary Next write trial hit wrk’s default two-second timeout; that partial matrix was discarded and the final matrix uses the same ten-second timeout for every implementation.

All timed runs reject socket errors and non-success responses. Independent Python checks compare every catalog cell/checksum/token and fresh timestamps; dashboard checks compare all owner-filtered task rows and CSRF tokens, reject tampered sessions/cross-origin mutations, and verify durable mutation side effects. Requests vary query tokens or mutation titles. Reads are uncached; mutations fully sync the file, rename it, and fully sync its parent directory. On macOS both native syncs request F_FULLFSYNC, matching [Node v25.7.0’s libuv implementation](https://github.com/nodejs/node/blob/v25.7.0/deps/uv/src/unix/fs.c). Earlier trials with weaker native flushes were discarded. Both apps use the same signed-session format, secret, 100 owned tasks plus 20 other-user tasks, and per-user mutation authorization. The Next fixture implements equivalent benchmark endpoints rather than a complete duplicate of the example application.

RSS is a post-load snapshot summed across the server process group, not peak memory. Startup is socket readiness with 5 ms polling, not cold OS-cache startup. Loopback wrk latency is closed-loop, includes queueing, and has coordinated-omission limitations. This Mac has no CPU affinity control; background load, thermal state, and OS caches are uncontrolled. These results do not establish browser performance, Linux performance, power consumption, OAuth-provider performance, or a universal advantage over Next.js. Baseline binaries precede this functionality expansion; their identities and all raw outputs are recorded in results.json.
