# Prepared-library authentication profile, before and after

5 shuffled 5-second trials per binary after a one-second warmup; one worker, 32 connections; uncached owner-filtered rendering of 100 tasks with 20 other-owner rows in the same file. Both semantic validation and durable mutation side effects are checked before/after each trial.

| Mode | Median req/s | CPU µs/request | Peak sampled RSS MiB | Binary MiB |
|---|---:|---:|---:|---:|
| prepared-before | 8,278.8 | 120.54 | 3.5 | 3.14 |
| prepared-current | 8,385.4 | 119.23 | 3.6 | 3.14 |
| monolithic-current | 8,523.4 | 117.09 | 3.5 | 3.08 |

Build order is shuffled within each round, rather than across all rounds, to reduce host-load drift. The host is shared; per-sample load averages are recorded.

| Round | Current vs saved prepared throughput | Current vs optional regressed throughput |
|---|---:|---:|
| 1 | +0.2% | — |
| 2 | +0.8% | — |
| 3 | +0.9% | — |
| 4 | +8.5% | — |
| 5 | +0.8% | — |

Median within-round throughput change versus saved prepared: +0.8%.

Current prepared throughput changes by +1.3% versus the saved prepared binary and -1.6% versus the current monolithic binary. The saved and current builds use the same application workload and HTTP options. Compiler/runtime versions can differ; this comparison does not isolate one compiler optimization. Host noise and machine-code layout can still affect the measured difference.

The compiler preserves checked return-alias contracts and pre-optimization library bodies, including its constant-argument and small-pure-helper policy across prepared boundaries. Cached objects still contain dependency code only; application checks and cache-integrity/invalidation regressions pass. The prepared build remains 1.6% below monolithic in this run. Host-load and short-trial limits from the main report apply. [Raw samples and binary identities](prepared-results.json).

Baseline identity: `build/realtime-opt-before/dashboard/server` is the saved earlier authenticated-fix prepared build copied from `build/auth-fixed/dashboard`. It is not a pre-async compiler baseline. The current and saved application workloads match; their runtime/compiler histories differ. No compiler optimization was added in this realtime task. The 1.6% prepared-versus-monolithic difference remains visible and is not described as zero.
