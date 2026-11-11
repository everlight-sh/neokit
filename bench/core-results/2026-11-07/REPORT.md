# NeoKit, Next and Astro: core server measurements

Production uncached SSR with matched table data, escaping, query variation, freshness and a small counter. One server worker/process, one wrk thread, identity encoding. NeoKit uses the normal prepared-library build, with current HTTP runtime options enabled.

Versions: NeoKit 0.1.0-dev.1; Next 16.1.1; Astro 7.4.7 / node adapter 11.1.11; Node v25.7.0. Host: Apple M1 Pro, 10 logical CPUs, 16.0 GiB RAM.

3 shuffled rounds, 4 seconds measured after one second of warmup per case; 10-second client timeout. Fixed-rate probes separately target 200 small pages/sec for 6 seconds.

| Framework | Small SSR req/s, C32 | p50 ms, C1 | p99 ms, C32 | Peak sampled RSS MiB, C32 | CPU µs/request, C32 | CPU % of one core at fixed rate |
|---|---:|---:|---:|---:|---:|---:|
| NeoKit (prepared) | 103,484.3 | 0.026 | 0.544 | 4.6 | 8.84 | 1.00 |
| Next | 898.6 | 1.430 | 74.580 | 365.0 | 1297.12 | 38.34 |
| Astro | 8,932.1 | 0.156 | 9.160 | 308.2 | 156.33 | 14.84 |

CPU at saturation is normalized per request: similar total CPU can deliver very different throughput. Fixed-rate CPU is a separate equal-load comparison, not CPU at peak throughput. RSS is the median of each trial’s peak sampled process-group RSS, not a guaranteed instantaneous high-water mark.

| Workload | Connections | Framework | Median req/s | p50 ms | p99 ms | Sampled RSS MiB | CPU % one core | CPU µs/request |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| small-ssr | 1 | neokit | 33,109.2 | 0.026 | 0.062 | 5.0 | 35.79 | 10.87 |
| small-ssr | 1 | next | 660.8 | 1.430 | 2.610 | 322.9 | 107.00 | 1632.65 |
| small-ssr | 1 | astro | 5,633.2 | 0.156 | 2.830 | 285.5 | 105.39 | 200.86 |
| small-ssr | 32 | neokit | 103,484.3 | 0.206 | 0.544 | 4.6 | 91.03 | 8.84 |
| small-ssr | 32 | next | 898.6 | 34.410 | 74.580 | 365.0 | 116.27 | 1297.12 |
| small-ssr | 32 | astro | 8,932.1 | 3.290 | 9.160 | 308.2 | 139.14 | 156.33 |
| large-ssr | 1 | neokit | 12,352.3 | 0.074 | 0.129 | 3.3 | 57.68 | 46.98 |
| large-ssr | 1 | next | 33.0 | 29.690 | 40.680 | 334.2 | 110.70 | 33609.02 |
| large-ssr | 1 | astro | 614.5 | 1.520 | 3.550 | 251.2 | 99.92 | 1638.21 |
| large-ssr | 32 | neokit | 20,824.9 | 1.500 | 3.060 | 3.0 | 99.04 | 47.66 |
| large-ssr | 32 | next | 22.6 | 759.570 | 3970.000 | 406.4 | 113.78 | 51428.57 |
| large-ssr | 32 | astro | 770.4 | 39.050 | 213.130 | 327.8 | 104.82 | 1364.23 |
| interactive-ssr | 1 | neokit | 29,724.4 | 0.027 | 0.232 | 4.7 | 37.72 | 12.48 |
| interactive-ssr | 1 | next | 345.5 | 2.820 | 4.430 | 355.2 | 109.53 | 3184.12 |
| interactive-ssr | 1 | astro | 3,106.3 | 0.294 | 1.610 | 284.4 | 92.97 | 293.44 |
| interactive-ssr | 32 | neokit | 89,215.4 | 0.315 | 7.220 | 3.6 | 96.36 | 10.82 |
| interactive-ssr | 32 | next | 395.6 | 78.220 | 249.990 | 384.6 | 116.90 | 2956.69 |
| interactive-ssr | 32 | astro | 5,064.0 | 6.050 | 13.100 | 315.0 | 125.60 | 248.73 |

| Framework | Socket readiness ms | First HTTP readiness ms | Small HTML bytes | Large HTML bytes | Interactive HTML bytes |
|---|---:|---:|---:|---:|---:|
| neokit | 8.54 | 9.08 | 967 | 104,965 | 6,051 |
| next | 217.96 | 343.46 | 6,786 | 410,432 | 26,082 |
| astro | 202.86 | 219.99 | 1,750 | 105,549 | 7,193 |

## Scope and limits

Every trial validates rendered cells, checksum, UTF-8/escaping, changed query tokens and fresh timestamps before and after timing. Socket errors and non-success responses fail the run. Workloads use 3, 1000 and 50 rows. Browser asset loading, hydration, database writes, auth, distributed caches, build speed, Linux performance and full feature parity are not measured by this matrix.

HTML wire sizes differ: Next includes App Router/RSC serialized data and bootstrap, while Astro and NeoKit use HTML with small widget modules. Those architectural costs are included. CPU comes from process-group CPU-time deltas using ps; 10 ms clock resolution limits low-CPU accuracy. Sampling RSS at 250 ms can miss brief peaks. Already-exited child CPU is not included. CPU can exceed 100% because Node/V8 background threads can use multiple cores despite one server process. Pre-trial semantic validation touches all three routes, so RSS includes a warmed fixture rather than an isolated cold small-page route. Startup means fresh process readiness with warm OS caches, not a cold machine.

All servers and the client share this busy Mac, with no affinity control, controlled thermals or formal confidence intervals. Tests are interleaved to reduce ordering bias, not eliminate it. Do not extrapolate these local fixtures into a universal framework ranking. Version numbers identify the actual installed packages; this is not an assertion that all are the latest releases. [Raw samples and binary/source identities](results.json), [summary JSON](summary.json), [throughput/resource CSV](samples.csv), [fixed-rate CSV](fixed-rate.csv), and [actual-browser checks](browser-checks.json) accompany the report. [The prepared-library authentication supplement](PREPARED.md) separately checks its remaining throughput gap.
