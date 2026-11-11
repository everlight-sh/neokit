# Prepared-library authentication profile, before and after

3 shuffled 5-second trials per binary after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.

| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB | Binary MiB |
|---|---:|---:|---:|---:|
| prepared-before | 6,261.9 | 139.46 | 3.5 | 3.14 |
| prepared-current | 7,097.6 | 132.58 | 3.5 | 3.14 |
| monolithic-current | 7,813.7 | 124.21 | 3.5 | 3.08 |

Build order is shuffled within each round, rather than across all rounds, to reduce host-load drift. The host is shared; per-sample load averages are recorded.

| Round | Fixed vs pre-async throughput | Fixed vs regressed async throughput |
|---|---:|---:|
| 1 | +23.6% | — |
| 2 | +4.3% | — |
| 3 | -3.2% | — |

Median within-round throughput change versus pre-async: +4.3%.

Current prepared throughput changes by +13.3% versus the saved prepared binary and -9.2% versus the current monolithic binary. The saved and current builds use the same application workload and HTTP options. Compiler/runtime versions can differ; this comparison does not isolate one compiler optimization. Host noise and machine-code layout can still affect the measured difference.

The compiler preserves checked return-alias contracts and pre-optimization library bodies, including its constant-argument and small-pure-helper policy across prepared boundaries. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. The prepared build remains 9.2% below monolithic in this run. Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).
