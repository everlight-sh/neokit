# Prepared-library authentication profile, before and after

4 shuffled 5-second trials per binary after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.

| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB | Binary MiB |
|---|---:|---:|---:|---:|
| prepared-before | 7,176.8 | 133.50 | 3.5 | 3.14 |
| prepared-current | 7,111.4 | 132.65 | 3.5 | 3.14 |
| monolithic-current | 7,442.6 | 129.08 | 3.5 | 3.08 |

Build order is shuffled within each round, rather than across all rounds, to reduce host-load drift. The host is shared; per-sample load averages are recorded.

| Round | Fixed vs pre-async throughput | Fixed vs regressed async throughput |
|---|---:|---:|
| 1 | +9.2% | — |
| 2 | -1.3% | — |
| 3 | -8.6% | — |
| 4 | +1.8% | — |

Median within-round throughput change versus pre-async: +0.2%.

Current prepared throughput changes by -0.9% versus the saved prepared binary and -4.5% versus the current monolithic binary. The saved and current builds use the same application workload and HTTP options. Compiler/runtime versions can differ; this comparison does not isolate one compiler optimization. Host noise and machine-code layout can still affect the measured difference.

The compiler preserves checked return-alias contracts and pre-optimization library bodies, including its constant-argument and small-pure-helper policy across prepared boundaries. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. The prepared build remains 4.5% below monolithic in this run. Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).
