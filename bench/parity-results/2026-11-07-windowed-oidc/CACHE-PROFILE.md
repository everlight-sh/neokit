# Prepared-library build profile check

Measured binaries pass the independent semantic checks from the main benchmark. This supplement compares their server throughput: three shuffled two-second trials after a one-second warmup, 32 connections, one worker and one wrk thread, identity encoding and a ten-second client timeout. Dashboard builds also pass the login/authorization/durable-write integration checks recorded with the verification report.

| Workload | Prepared req/s | Monolithic req/s | Prepared / monolithic |
|---|---:|---:|---:|
| authenticated-db-ssr | 6,245.6 | 7,232.6 | 0.86× |

Short local trials and near-full-disk/host-load limitations still apply; this is not a formal confidence interval or a browser benchmark. Both modes retain Neo checks. Prepared libraries are the normal cache-enabled build path; monolithic uses NEOKIT_LIBRARY_CACHE=off. Raw outputs and binary identities are in [cache-profile.json](cache-profile.json).
