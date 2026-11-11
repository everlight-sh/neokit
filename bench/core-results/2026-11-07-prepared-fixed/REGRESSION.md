# Additional native regression check

After the three-framework run, five shuffled four-second trials per binary repeated the small SSR workload at 32 connections, one worker and one wrk thread, after one second of warmup. Each fresh server passed the full semantic/freshness oracle before and after its timed trial. The only workload varied between trials was the binary.

| Binary | Median req/s | Trial range req/s | CPU µs/request | Median peak sampled RSS MiB |
|---|---:|---:|---:|---:|
| before | 72,877.4 | 70,781.1–100,301.8 | 12.98 | 4.3 |
| fixed | 74,334.2 | 72,604.0–82,989.9 | 12.17 | 4.9 |

Fixed median throughput is +2.0% relative to the saved prepared binary. This check found no small-page throughput regression. Both binaries varied widely on the busy host; a small difference is not evidence of a universal gain.

The core report retains its original 54 throughput and nine fixed-rate trials; these extra ten samples do not replace any result or select a faster run. Comparing its medians to an earlier session cannot isolate a compiler effect under this host variability. [Raw paired-check samples and identities](catalog-regression.json).
