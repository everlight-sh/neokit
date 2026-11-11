# Prepared-library authentication profile, before and after

5 shuffled 5-second trials per binary after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.

| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB | Binary MiB |
|---|---:|---:|---:|---:|
| prepared-before | 6,590.2 | 151.43 | 3.7 | 3.23 |
| prepared-current | 7,323.3 | 136.01 | 3.5 | 3.33 |
| monolithic-current | 7,222.9 | 137.72 | 3.5 | 3.07 |

Current prepared throughput changes by +11.1% versus the saved prepared binary and +1.4% versus the current monolithic binary. The saved and current builds use the same application source and HTTP options; the prepared compiler implementation changes. Host noise and machine-code layout can still affect the measured difference.

The compiler preserves checked return-alias contracts and pre-optimization library bodies, including its constant-argument and small-pure-helper policy across prepared boundaries. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. These trials show no prepared throughput deficit; the small advantage over monolithic does not establish a general speed advantage. Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).
