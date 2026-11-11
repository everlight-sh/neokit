# NeoKit functionality expansion: local production measurements

Host: macOS-27.2-arm64-arm-64bit-Mach-O, Apple M1 Pro. Next 16.1.1, Node v25.7.0. One server worker/process, one wrk thread, identity encoding; 3 shuffled rounds of 2 seconds after a one-second warmup, with a 10-second client timeout.

NeoKit uses release/O3 monolithic builds (`NEOKIT_LIBRARY_CACHE=off`). Next uses its production webpack build with one build worker. The saved pre-expansion baseline is identified by binary hash; these figures compare the complete before/after binaries, not an isolated estimate of each new feature’s cost.

| Workload at 32 connections | NeoKit req/s | Next req/s | Throughput ratio | NeoKit vs saved baseline |
|---|---:|---:|---:|---:|
| small-ssr | 96,277.6 | 808.2 | 119.1× | -6.9% |
| large-ssr | 20,724.0 | 15.8 | 1307.5× | +2.5% |
| interactive-ssr | 90,171.8 | 349.2 | 258.3× | -5.0% |
| authenticated-db-ssr | 6,800.2 | 130.3 | 52.2× | new capability |
| durable-mutation | 84.3 | 64.9 | 1.3× | new capability |

Catalog throughput changes range from -6.9% to +2.5% against the saved baseline at 32 connections. Fully flushed mutations are storage-bound and show a much smaller advantage than SSR. See median and p99 latency separately; no universal latency improvement is implied. Next is the locally installed 16.1.1 production build, not an assertion about the latest release. These are short local trials without formal confidence intervals. The APFS volume had less than 1 GiB free throughout this work, making write timings especially provisional.

| Workload | Connections | Framework | Median req/s | Median p50 ms | Median p99 ms | Median RSS MiB |
|---|---:|---|---:|---:|---:|---:|
| small-ssr | 1 | baseline-neokit | 34331.6 | 0.025 | 0.067 | 2.2 |
| small-ssr | 1 | neokit | 33611.3 | 0.025 | 0.067 | 2.3 |
| small-ssr | 1 | next | 579.0 | 1.670 | 3.060 | 297.7 |
| small-ssr | 32 | baseline-neokit | 103446.3 | 0.199 | 0.481 | 2.8 |
| small-ssr | 32 | neokit | 96277.6 | 0.205 | 0.621 | 2.9 |
| small-ssr | 32 | next | 808.2 | 36.560 | 127.360 | 336.9 |
| large-ssr | 1 | baseline-neokit | 12194.6 | 0.074 | 0.137 | 2.2 |
| large-ssr | 1 | neokit | 11711.2 | 0.077 | 0.152 | 2.3 |
| large-ssr | 1 | next | 33.0 | 29.490 | 43.190 | 314.5 |
| large-ssr | 32 | baseline-neokit | 20216.2 | 1.510 | 3.120 | 2.8 |
| large-ssr | 32 | neokit | 20724.0 | 1.500 | 3.070 | 2.9 |
| large-ssr | 32 | next | 15.8 | 521.350 | 2000.000 | 370.4 |
| interactive-ssr | 1 | baseline-neokit | 31451.2 | 0.027 | 0.069 | 2.2 |
| interactive-ssr | 1 | neokit | 29475.2 | 0.027 | 0.102 | 2.3 |
| interactive-ssr | 1 | next | 322.2 | 3.020 | 4.890 | 309.5 |
| interactive-ssr | 32 | baseline-neokit | 94919.2 | 0.267 | 0.681 | 3.1 |
| interactive-ssr | 32 | neokit | 90171.8 | 0.272 | 0.850 | 3.2 |
| interactive-ssr | 32 | next | 349.2 | 84.130 | 359.340 | 330.9 |
| authenticated-db-ssr | 1 | neokit | 5730.4 | 0.165 | 0.269 | 2.8 |
| authenticated-db-ssr | 1 | next | 130.6 | 7.460 | 10.230 | 269.9 |
| authenticated-db-ssr | 32 | neokit | 6800.2 | 4.470 | 9.160 | 3.4 |
| authenticated-db-ssr | 32 | next | 130.3 | 216.440 | 1060.000 | 338.5 |
| durable-mutation | 1 | neokit | 96.7 | 9.990 | 18.050 | 2.8 |
| durable-mutation | 1 | next | 84.1 | 11.600 | 21.230 | 128.1 |
| durable-mutation | 32 | neokit | 84.3 | 323.190 | 656.550 | 3.4 |
| durable-mutation | 32 | next | 64.9 | 148.640 | 1670.000 | 132.6 |

| Representative decoded response | NeoKit bytes | Next bytes |
|---|---:|---:|
| small-ssr | 967 | 6,786 |
| large-ssr | 104,965 | 410,432 |
| interactive-ssr | 6,051 | 26,082 |
| authenticated-db-ssr | 43,226 | 122,645 |

The fixtures match rendered table/task data and behavior under test, not wire bytes. Next includes its App Router/RSC bootstrap and serialized component data; NeoKit sends HTML with optional widget modules. The throughput comparison includes this architectural payload difference. External browser asset loading is not part of wrk throughput. [Actual-browser functional checks](../../browser-results/2026-11-07/REPORT.md) are recorded separately. A preliminary Next write trial hit wrk’s default two-second timeout; that partial matrix was discarded and the final matrix uses the same ten-second timeout for every implementation.

All timed runs reject socket errors and non-success responses. Independent Python checks compare every catalog cell/checksum/token and fresh timestamps; dashboard checks compare all owner-filtered task rows and CSRF tokens, reject tampered sessions/cross-origin mutations, and verify durable mutation side effects. Requests vary query tokens or mutation titles. Reads are uncached; mutations fully sync the file, rename it, and fully sync its parent directory. On macOS both native syncs request F_FULLFSYNC, matching [Node v25.7.0’s libuv implementation](https://github.com/nodejs/node/blob/v25.7.0/deps/uv/src/unix/fs.c). Earlier trials with weaker native flushes were discarded. Both apps use the same signed-session format, secret, 100 owned tasks plus 20 other-user tasks, and per-user mutation authorization. The Next fixture implements equivalent benchmark endpoints rather than a complete duplicate of the example application.

RSS is a post-load snapshot summed across the server process group, not peak memory. Startup is socket readiness with 5 ms polling, not cold OS-cache startup. Loopback wrk latency is closed-loop, includes queueing, and has coordinated-omission limitations. This Mac has no CPU affinity control; background load, thermal state, and OS caches are uncontrolled. These results do not establish browser performance, Linux performance, power consumption, OAuth-provider performance, or a universal advantage over Next.js. Baseline binaries precede this functionality expansion; their identities and all raw outputs are recorded in results.json.

[Prepared-library build profile checks](CACHE-PROFILE.md) validate the normal cache-enabled path separately; those throughput samples had substantial background CPU activity.
