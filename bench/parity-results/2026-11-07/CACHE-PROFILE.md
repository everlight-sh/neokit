# Prepared-library build profile check

Both modes pass the native features and authenticated dashboard integrations. This supplement compares their server throughput: three shuffled two-second trials after a one-second warmup, 32 connections, one worker and one wrk thread, identity encoding and a ten-second client timeout. The independent semantic checks are the same as the main benchmark.

| Workload | Prepared req/s | Monolithic req/s | Prepared / monolithic |
|---|---:|---:|---:|
| small-ssr | 84,451.9 | 87,290.2 | 0.97× |
| large-ssr | 17,154.3 | 19,490.2 | 0.88× |
| interactive-ssr | 76,529.4 | 62,667.5 | 1.22× |
| authenticated-db-ssr | 4,416.4 | 5,602.7 | 0.79× |

After the supplement, Postman helpers and system services occupied several CPU cores. Absolute rates varied substantially, including between repeated runs of the same binary. These profile ratios are provisional and do not isolate compiler-cache overhead. Short local trials and near-full-disk/host-load limitations still apply; this is not a formal confidence interval or a browser benchmark. Both modes retain Neo checks. Prepared libraries are the normal cache-enabled build path; monolithic uses NEOKIT_LIBRARY_CACHE=off. Raw outputs and binary identities are in [cache-profile.json](cache-profile.json).
