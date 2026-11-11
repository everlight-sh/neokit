# NeoKit, Next and Astro: core server measurements

Production uncached SSR with matched table data, escaping, query variation, freshness and a small counter. One server worker/process, one wrk thread, identity encoding. NeoKit uses the normal prepared-library build, with current HTTP runtime options enabled.

Versions: NeoKit 0.1.0-dev.1; Next 16.1.1; Astro 7.4.7 / node adapter 11.1.11; Node v25.7.0. Host: Apple M1 Pro, 10 logical CPUs, 16.0 GiB RAM.

3 shuffled rounds, 4 seconds measured after one second of warmup per case; 10-second client timeout. Fixed-rate probes separately target 200 small pages/sec for 6 seconds.

| Framework | Small SSR req/s, C32 | p50 ms, C1 | p99 ms, C32 | Peak sampled RSS MiB, C32 | CPU µs/request, C32 | CPU % of one core at fixed rate |
|---|---:|---:|---:|---:|---:|---:|
| NeoKit (prepared) | 120,852.0 | 0.024 | 0.440 | 5.1 | 7.37 | 0.83 |
| Next | 973.3 | 1.410 | 50.610 | 380.2 | 1219.89 | 48.00 |
| Astro | 9,157.3 | 0.150 | 8.530 | 308.9 | 159.51 | 14.83 |

CPU at saturation is normalized per request: similar total CPU can deliver very different throughput. Fixed-rate CPU is a separate equal-load comparison, not CPU at peak throughput. RSS is the median of each trial’s peak sampled process-group RSS, not a guaranteed instantaneous high-water mark.

| Workload | Connections | Framework | Median req/s | p50 ms | p99 ms | Sampled RSS MiB | CPU % one core | CPU µs/request |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| small-ssr | 1 | neokit | 36,787.8 | 0.024 | 0.055 | 5.2 | 34.32 | 9.35 |
| small-ssr | 1 | next | 661.7 | 1.410 | 2.880 | 342.3 | 107.65 | 1609.58 |
| small-ssr | 1 | astro | 5,793.5 | 0.150 | 2.330 | 285.3 | 109.49 | 205.04 |
| small-ssr | 32 | neokit | 120,852.0 | 0.179 | 0.440 | 5.1 | 88.81 | 7.37 |
| small-ssr | 32 | next | 973.3 | 32.510 | 50.610 | 380.2 | 118.47 | 1219.89 |
| small-ssr | 32 | astro | 9,157.3 | 3.190 | 8.530 | 308.9 | 141.29 | 159.51 |
| large-ssr | 1 | neokit | 12,173.3 | 0.073 | 0.158 | 3.3 | 56.96 | 47.05 |
| large-ssr | 1 | next | 35.0 | 28.200 | 35.930 | 332.8 | 109.97 | 31489.36 |
| large-ssr | 1 | astro | 625.6 | 1.450 | 3.520 | 243.8 | 102.62 | 1580.84 |
| large-ssr | 32 | neokit | 20,603.0 | 1.490 | 3.190 | 3.0 | 97.51 | 47.35 |
| large-ssr | 32 | next | 22.5 | 751.400 | 3830.000 | 417.1 | 115.46 | 51648.35 |
| large-ssr | 32 | astro | 750.2 | 40.170 | 279.830 | 344.9 | 104.53 | 1467.55 |
| interactive-ssr | 1 | neokit | 33,259.2 | 0.025 | 0.069 | 5.0 | 37.97 | 11.28 |
| interactive-ssr | 1 | next | 375.5 | 2.550 | 4.200 | 353.2 | 110.65 | 2948.21 |
| interactive-ssr | 1 | astro | 3,143.2 | 0.291 | 1.510 | 281.0 | 95.17 | 308.01 |
| interactive-ssr | 32 | neokit | 97,017.1 | 0.258 | 0.720 | 3.8 | 96.87 | 9.83 |
| interactive-ssr | 32 | next | 404.2 | 76.550 | 227.060 | 390.6 | 116.83 | 2898.46 |
| interactive-ssr | 32 | astro | 5,657.7 | 5.430 | 11.630 | 312.4 | 128.98 | 228.46 |

| Framework | Socket readiness ms | First HTTP readiness ms | Small HTML bytes | Large HTML bytes | Interactive HTML bytes |
|---|---:|---:|---:|---:|---:|
| neokit | 8.86 | 9.61 | 967 | 104,965 | 6,051 |
| next | 218.21 | 339.31 | 6,786 | 410,432 | 26,082 |
| astro | 199.32 | 216.00 | 1,750 | 105,549 | 7,193 |

## Scope and limits

Every trial validates rendered cells, checksum, UTF-8/escaping, changed query tokens and fresh timestamps before and after timing. Socket errors and non-success responses fail the run. Workloads use 3, 1000 and 50 rows. Browser asset loading, hydration, database writes, auth, distributed caches, build speed, Linux performance and full feature parity are not measured by this matrix.

HTML wire sizes differ: Next includes App Router/RSC serialized data and bootstrap, while Astro and NeoKit use HTML with small widget modules. Those architectural costs are included. CPU comes from process-group CPU-time deltas using ps; 10 ms clock resolution limits low-CPU accuracy. Sampling RSS at 250 ms can miss brief peaks. Already-exited child CPU is not included. CPU can exceed 100% because Node/V8 background threads can use multiple cores despite one server process. Pre-trial semantic validation touches all three routes, so RSS includes a warmed fixture rather than an isolated cold small-page route. Startup means fresh process readiness with warm OS caches, not a cold machine.

All servers and the client share this busy Mac, with no affinity control, controlled thermals or formal confidence intervals. Tests are interleaved to reduce ordering bias, not eliminate it. Do not extrapolate these local fixtures into a universal framework ranking. Version numbers identify the actual installed packages; this is not an assertion that all are the latest releases. [Raw samples and binary/source identities](results.json), [summary JSON](summary.json), [throughput/resource CSV](samples.csv), [fixed-rate CSV](fixed-rate.csv), and [separate async/browser checks](../../async-results/2026-11-08/browser-final.json) accompany the report. [The prepared-library authentication supplement](PREPARED.md) separately checks the compiler fix against a saved prepared binary and the current monolithic build.
