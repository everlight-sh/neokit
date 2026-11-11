# Prepared-library authentication profile, before and after

Three interleaved four-second trials after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.

| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB |
|---|---:|---:|---:|
| prepared-before | 6,213.7 | 159.75 | 3.3 |
| prepared-current | 6,322.4 | 156.23 | 3.7 |
| monolithic-current | 6,980.9 | 141.50 | 3.5 |

Current prepared throughput changes by +1.8% versus the saved prepared binary and -9.4% versus the current monolithic binary. These are complete-binary measurements: the current builds also enable the in-progress HTTP runtime options. They do not isolate a compiler-only causal effect.

The compiler now exposes larger optimization-only library bodies and restores its small-pure-helper inlining policy across the prepared boundary. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. Any remaining gap must be reported, not described as fully fixed. Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).
