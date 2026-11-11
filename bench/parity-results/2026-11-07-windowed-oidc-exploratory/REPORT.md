# NeoKit functionality expansion: local production measurements

Exploratory only: this matrix overlapped regression builds and VM startup. Use the subsequent `2026-11-07-windowed-oidc` matrix for the final comparison.

Host: macOS-27.2-arm64-arm-64bit-Mach-O, Apple M1 Pro. Next 16.1.1, Node v25.7.0. One server worker/process, one wrk thread, identity encoding; 3 shuffled rounds of 2 seconds after a one-second warmup, with a 10-second client timeout.

NeoKit uses release/O3 monolithic builds (`NEOKIT_LIBRARY_CACHE=off`). Next uses its production webpack build with one build worker. The saved pre-expansion baseline is identified by binary hash; these figures compare the complete before/after binaries, not an isolated estimate of each new feature’s cost.

| Workload at 32 connections | NeoKit req/s | Next req/s | Throughput ratio | NeoKit vs saved baseline |
|---|---:|---:|---:|---:|
| small-ssr | 105,517.6 | 860.9 | 122.6× | +1.3% |
| large-ssr | 20,823.9 | 15.8 | 1321.3× | +0.3% |
| interactive-ssr | 94,916.1 | 372.6 | 254.7× | +0.9% |
| authenticated-db-ssr | 7,152.3 | 131.2 | 54.5× | new capability |
| durable-mutation | 89.3 | 72.0 | 1.2× | new capability |

Catalog throughput changes range from +0.3% to +1.3% against the saved baseline at 32 connections. Fully flushed mutations are storage-bound and show a much smaller advantage than SSR. See median and p99 latency separately; no universal latency improvement is implied. Next is the locally installed 16.1.1 production build, not an assertion about the latest release. These are short local trials without formal confidence intervals. Host load and disk headroom are recorded in results.json; storage and background activity make write timings especially provisional.

| Workload | Connections | Framework | Median req/s | Median p50 ms | Median p99 ms | Median RSS MiB |
|---|---:|---|---:|---:|---:|---:|
| small-ssr | 1 | baseline-neokit | 34183.6 | 0.025 | 0.070 | 2.2 |
| small-ssr | 1 | neokit | 33669.0 | 0.025 | 0.065 | 2.3 |
| small-ssr | 1 | next | 496.9 | 1.800 | 6.090 | 303.2 |
| small-ssr | 32 | baseline-neokit | 104190.6 | 0.196 | 0.493 | 2.8 |
| small-ssr | 32 | neokit | 105517.6 | 0.198 | 0.492 | 2.9 |
| small-ssr | 32 | next | 860.9 | 35.820 | 125.300 | 344.4 |
| large-ssr | 1 | baseline-neokit | 12216.9 | 0.074 | 0.143 | 2.2 |
| large-ssr | 1 | neokit | 12238.0 | 0.074 | 0.137 | 2.3 |
| large-ssr | 1 | next | 33.2 | 29.360 | 45.280 | 314.9 |
| large-ssr | 32 | baseline-neokit | 20751.6 | 1.500 | 3.070 | 2.8 |
| large-ssr | 32 | neokit | 20823.9 | 1.500 | 3.060 | 2.9 |
| large-ssr | 32 | next | 15.8 | 499.690 | 1890.000 | 351.9 |
| interactive-ssr | 1 | baseline-neokit | 31298.6 | 0.027 | 0.070 | 2.2 |
| interactive-ssr | 1 | neokit | 30773.5 | 0.027 | 0.072 | 2.3 |
| interactive-ssr | 1 | next | 322.8 | 3.000 | 4.920 | 303.3 |
| interactive-ssr | 32 | baseline-neokit | 94086.5 | 0.279 | 0.684 | 3.1 |
| interactive-ssr | 32 | neokit | 94916.1 | 0.277 | 0.679 | 3.2 |
| interactive-ssr | 32 | next | 372.6 | 80.910 | 347.080 | 330.8 |
| authenticated-db-ssr | 1 | neokit | 5800.9 | 0.164 | 0.262 | 2.8 |
| authenticated-db-ssr | 1 | next | 135.6 | 7.250 | 9.410 | 262.2 |
| authenticated-db-ssr | 32 | neokit | 7152.3 | 4.310 | 8.730 | 3.4 |
| authenticated-db-ssr | 32 | next | 131.2 | 211.400 | 1060.000 | 320.9 |
| durable-mutation | 1 | neokit | 99.6 | 9.380 | 14.950 | 2.8 |
| durable-mutation | 1 | next | 81.1 | 11.460 | 20.060 | 127.8 |
| durable-mutation | 32 | neokit | 89.3 | 299.010 | 620.220 | 3.4 |
| durable-mutation | 32 | next | 72.0 | 150.870 | 1780.000 | 134.3 |

| Representative decoded response | NeoKit bytes | Next bytes |
|---|---:|---:|
| small-ssr | 967 | 6,786 |
| large-ssr | 104,965 | 410,432 |
| interactive-ssr | 6,051 | 26,082 |
| authenticated-db-ssr | 43,226 | 122,645 |

The fixtures match rendered table/task data and behavior under test, not wire bytes. Next includes its App Router/RSC bootstrap and serialized component data; NeoKit sends HTML with optional widget modules. The throughput comparison includes this architectural payload difference. External browser asset loading is not part of wrk throughput. [Actual-browser functional checks](../../browser-results/2026-11-07/REPORT.md) are recorded separately. A preliminary Next write trial hit wrk’s default two-second timeout; that partial matrix was discarded and the final matrix uses the same ten-second timeout for every implementation.

All timed runs reject socket errors and non-success responses. Independent Python checks compare every catalog cell/checksum/token and fresh timestamps; dashboard checks compare all owner-filtered task rows and CSRF tokens, reject tampered sessions/cross-origin mutations, and verify durable mutation side effects. Requests vary query tokens or mutation titles. Reads are uncached; mutations fully sync the file, rename it, and fully sync its parent directory. On macOS both native syncs request F_FULLFSYNC, matching [Node v25.7.0’s libuv implementation](https://github.com/nodejs/node/blob/v25.7.0/deps/uv/src/unix/fs.c). Earlier trials with weaker native flushes were discarded. Both apps use the same signed-session format, secret, 100 owned tasks plus 20 other-user tasks, and per-user mutation authorization. The Next fixture implements equivalent benchmark endpoints rather than a complete duplicate of the example application.

RSS is a post-load snapshot summed across the server process group, not peak memory. Startup is socket readiness with 5 ms polling, not cold OS-cache startup. Loopback wrk latency is closed-loop, includes queueing, and has coordinated-omission limitations. This Mac has no CPU affinity control; background load, thermal state, and OS caches are uncontrolled. These results do not establish browser performance, Linux performance, power consumption, OAuth-provider performance, or a universal advantage over Next.js. Baseline binaries precede this functionality expansion; their identities and all raw outputs are recorded in results.json.
