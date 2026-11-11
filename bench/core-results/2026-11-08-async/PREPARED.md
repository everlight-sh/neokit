# Prepared-library authentication profile, before and after

5 shuffled 5-second trials per binary after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.

| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB | Binary MiB |
|---|---:|---:|---:|---:|
| prepared-before | 7,148.9 | 138.18 | 3.5 | 3.33 |
| prepared-current | 6,560.6 | 149.74 | 3.4 | 2.32 |
| monolithic-current | 6,622.1 | 144.43 | 3.5 | 3.08 |

Current prepared throughput changes by -8.2% versus the saved prepared binary and -0.9% versus the current monolithic binary. The saved and current builds use the same application workload and HTTP options. Compiler/runtime versions can differ; this comparison does not isolate one compiler optimization. Host noise and machine-code layout can still affect the measured difference.

The compiler preserves checked return-alias contracts and pre-optimization library bodies, including its constant-argument and small-pure-helper policy across prepared boundaries. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. The prepared build remains 0.9% below monolithic in this run. Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).
