# NeoKit, Next and Astro: core server measurements

Production uncached SSR with matched table data, escaping, query variation, freshness and a small counter. One server worker/process, one wrk thread, identity encoding. NeoKit uses the normal prepared-library build, with current HTTP runtime options enabled.

Versions: NeoKit 0.1.0-dev.1; Next 16.1.1; Astro 7.4.7 / node adapter 11.1.11; Node v25.7.0. Host: Apple M1 Pro, 10 logical CPUs, 16.0 GiB RAM.

3 shuffled rounds, 4 seconds measured after one second of warmup per case; 10-second client timeout. Fixed-rate probes separately target 200 small pages/sec for 6 seconds.

| Framework | Small SSR req/s, C32 | p50 ms, C1 | p99 ms, C32 | Peak sampled RSS MiB, C32 | CPU µs/request, C32 | CPU % of one core at fixed rate |
|---|---:|---:|---:|---:|---:|---:|
| NeoKit (prepared) | 86,224.2 | 0.021 | 0.910 | 4.7 | 9.40 | 0.83 |
| Next | 933.9 | 1.710 | 61.150 | 369.3 | 1269.67 | 47.51 |
| Astro | 8,721.4 | 0.160 | 12.380 | 302.3 | 158.12 | 12.84 |

CPU at saturation is normalized per request: similar total CPU can deliver very different throughput. Fixed-rate CPU is a separate equal-load comparison, not CPU at peak throughput. RSS is the median of each trial’s peak sampled process-group RSS, not a guaranteed instantaneous high-water mark.

| Workload | Connections | Framework | Median req/s | p50 ms | p99 ms | Sampled RSS MiB | CPU % one core | CPU µs/request |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| small-ssr | 1 | neokit | 33,642.0 | 0.021 | 0.191 | 3.4 | 35.03 | 10.08 |
| small-ssr | 1 | next | 540.3 | 1.710 | 4.670 | 291.9 | 105.04 | 1948.58 |
| small-ssr | 1 | astro | 5,348.0 | 0.160 | 2.550 | 278.9 | 113.93 | 205.17 |
| small-ssr | 32 | neokit | 86,224.2 | 0.204 | 0.910 | 4.7 | 81.79 | 9.40 |
| small-ssr | 32 | next | 933.9 | 33.400 | 61.150 | 369.3 | 117.77 | 1269.67 |
| small-ssr | 32 | astro | 8,721.4 | 3.290 | 12.380 | 302.3 | 138.16 | 158.12 |
| large-ssr | 1 | neokit | 12,369.6 | 0.071 | 0.755 | 3.2 | 57.68 | 46.73 |
| large-ssr | 1 | next | 31.3 | 29.780 | 65.220 | 350.2 | 106.29 | 34015.75 |
| large-ssr | 1 | astro | 563.8 | 1.630 | 6.640 | 214.6 | 99.45 | 1769.03 |
| large-ssr | 32 | neokit | 20,575.5 | 1.510 | 3.080 | 3.0 | 98.57 | 48.01 |
| large-ssr | 32 | next | 19.3 | 750.720 | 3870.000 | 386.6 | 110.20 | 57179.49 |
| large-ssr | 32 | astro | 689.6 | 40.600 | 279.560 | 311.7 | 99.85 | 1454.41 |
| interactive-ssr | 1 | neokit | 34,161.7 | 0.024 | 0.090 | 5.0 | 37.97 | 11.14 |
| interactive-ssr | 1 | next | 279.1 | 3.380 | 37.210 | 318.1 | 107.00 | 3892.86 |
| interactive-ssr | 1 | astro | 2,987.9 | 0.301 | 1.470 | 263.2 | 93.44 | 323.27 |
| interactive-ssr | 32 | neokit | 78,633.2 | 0.294 | 2.340 | 3.5 | 90.04 | 11.30 |
| interactive-ssr | 32 | next | 347.0 | 82.970 | 297.830 | 364.3 | 113.32 | 3278.81 |
| interactive-ssr | 32 | astro | 5,157.0 | 6.010 | 14.860 | 317.0 | 126.21 | 247.78 |

| Framework | Socket readiness ms | First HTTP readiness ms | Small HTML bytes | Large HTML bytes | Interactive HTML bytes |
|---|---:|---:|---:|---:|---:|
| neokit | 8.84 | 9.42 | 967 | 104,965 | 6,051 |
| next | 302.55 | 481.10 | 6,786 | 410,432 | 26,082 |
| astro | 247.01 | 263.97 | 1,750 | 105,549 | 7,193 |

## Scope and limits

Every trial validates rendered cells, checksum, UTF-8/escaping, changed query tokens and fresh timestamps before and after timing. Socket errors and non-success responses fail the run. Workloads use 3, 1000 and 50 rows. Browser asset loading, hydration, database writes, auth, distributed caches, build speed, Linux performance and full feature parity are not measured by this matrix.

HTML wire sizes differ: Next includes App Router/RSC serialized data and bootstrap, while Astro and NeoKit use HTML with small widget modules. Those architectural costs are included. CPU comes from process-group CPU-time deltas using ps; 10 ms clock resolution limits low-CPU accuracy. Sampling RSS at 250 ms can miss brief peaks. Already-exited child CPU is not included. CPU can exceed 100% because Node/V8 background threads can use multiple cores despite one server process. Pre-trial semantic validation touches all three routes, so RSS includes a warmed fixture rather than an isolated cold small-page route. Startup means fresh process readiness with warm OS caches, not a cold machine.

All servers and the client share this busy Mac, with no affinity control, controlled thermals or formal confidence intervals. Tests are interleaved to reduce ordering bias, not eliminate it. Do not extrapolate these local fixtures into a universal framework ranking. Version numbers identify the actual installed packages; this is not an assertion that all are the latest releases. [Raw samples and binary/source identities](results.json), [summary JSON](summary.json), [throughput/resource CSV](samples.csv), [fixed-rate CSV](fixed-rate.csv), and [separate async/browser checks](../../async-results/2026-11-08/browser-final.json) accompany the report. [The prepared-library authentication supplement](PREPARED.md) separately checks the compiler fix against a saved prepared binary and the current monolithic build.
