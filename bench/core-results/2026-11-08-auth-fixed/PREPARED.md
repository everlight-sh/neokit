# Prepared-library authentication profile, before and after

7 shuffled 5-second trials per binary after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.

| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB | Binary MiB |
|---|---:|---:|---:|---:|
| prepared-before | 7,138.6 | 137.61 | 3.5 | 3.33 |
| prepared-current | 7,670.9 | 127.33 | 3.5 | 3.14 |
| monolithic-current | 7,966.5 | 122.66 | 3.5 | 3.08 |
| prepared-regressed | 6,605.7 | 147.71 | 3.4 | 2.32 |

Build order is shuffled within each round, rather than across all rounds, to reduce host-load drift. The host is shared; per-sample load averages are recorded.

| Round | Fixed vs pre-async throughput | Fixed vs regressed async throughput |
|---|---:|---:|
| 1 | +6.3% | +7.8% |
| 2 | +7.8% | +7.6% |
| 3 | +11.5% | +10.0% |
| 4 | +17.9% | +18.9% |
| 5 | +1.3% | +11.1% |
| 6 | +10.6% | +29.8% |
| 7 | +9.9% | +10.1% |

Median within-round throughput change versus pre-async: +9.9%. Versus regressed async: +10.1%.

Current prepared throughput changes by +7.5% versus the saved prepared binary and -3.7% versus the current monolithic binary. The saved and current builds use the same application workload and HTTP options. Compiler/runtime versions can differ; this comparison does not isolate one compiler optimization. Host noise and machine-code layout can still affect the measured difference.

The compiler preserves checked return-alias contracts and pre-optimization library bodies, including its constant-argument and small-pure-helper policy across prepared boundaries. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. The prepared build remains 3.7% below monolithic in this run. Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).
