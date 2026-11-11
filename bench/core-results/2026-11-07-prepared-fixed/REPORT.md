# NeoKit, Next and Astro: core server measurements

Production uncached SSR with matched table data, escaping, query variation, freshness and a small counter. One server worker/process, one wrk thread, identity encoding. NeoKit uses the normal prepared-library build, with current HTTP runtime options enabled.

Versions: NeoKit 0.1.0-dev.1; Next 16.1.1; Astro 7.4.7 / node adapter 11.1.11; Node v25.7.0. Host: Apple M1 Pro, 10 logical CPUs, 16.0 GiB RAM.

3 shuffled rounds, 4 seconds measured after one second of warmup per case; 10-second client timeout. Fixed-rate probes separately target 200 small pages/sec for 6 seconds.

| Framework | Small SSR req/s, C32 | p50 ms, C1 | p99 ms, C32 | Peak sampled RSS MiB, C32 | CPU µs/request, C32 | CPU % of one core at fixed rate |
|---|---:|---:|---:|---:|---:|---:|
| NeoKit (prepared) | 88,696.5 | 0.026 | 0.538 | 5.5 | 9.71 | 1.00 |
| Next | 942.5 | 1.420 | 60.110 | 379.3 | 1250.00 | 40.50 |
| Astro | 7,695.0 | 0.169 | 9.820 | 302.9 | 187.40 | 15.34 |

CPU at saturation is normalized per request: similar total CPU can deliver very different throughput. Fixed-rate CPU is a separate equal-load comparison, not CPU at peak throughput. RSS is the median of each trial’s peak sampled process-group RSS, not a guaranteed instantaneous high-water mark.

| Workload | Connections | Framework | Median req/s | p50 ms | p99 ms | Sampled RSS MiB | CPU % one core | CPU µs/request |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| small-ssr | 1 | neokit | 32,378.1 | 0.026 | 0.056 | 4.9 | 36.02 | 11.14 |
| small-ssr | 1 | next | 659.4 | 1.420 | 2.570 | 326.5 | 108.11 | 1643.32 |
| small-ssr | 1 | astro | 5,440.5 | 0.169 | 2.310 | 284.7 | 110.47 | 209.04 |
| small-ssr | 32 | neokit | 88,696.5 | 0.219 | 0.538 | 5.5 | 85.91 | 9.71 |
| small-ssr | 32 | next | 942.5 | 33.550 | 60.110 | 379.3 | 118.51 | 1250.00 |
| small-ssr | 32 | astro | 7,695.0 | 3.850 | 9.820 | 302.9 | 142.08 | 187.40 |
| large-ssr | 1 | neokit | 11,701.0 | 0.079 | 0.127 | 3.2 | 58.41 | 50.03 |
| large-ssr | 1 | next | 33.6 | 29.520 | 37.450 | 328.2 | 110.58 | 33358.21 |
| large-ssr | 1 | astro | 609.5 | 1.590 | 2.770 | 245.9 | 102.48 | 1646.68 |
| large-ssr | 32 | neokit | 18,021.8 | 1.730 | 3.600 | 3.0 | 99.29 | 55.21 |
| large-ssr | 32 | next | 19.2 | 821.580 | 3960.000 | 401.8 | 114.03 | 59615.38 |
| large-ssr | 32 | astro | 669.6 | 46.990 | 248.650 | 309.9 | 106.62 | 1596.94 |
| interactive-ssr | 1 | neokit | 30,534.3 | 0.028 | 0.061 | 4.7 | 37.48 | 12.30 |
| interactive-ssr | 1 | next | 353.4 | 2.750 | 4.300 | 354.7 | 110.00 | 3117.69 |
| interactive-ssr | 1 | astro | 3,215.7 | 0.285 | 1.360 | 284.5 | 96.61 | 299.58 |
| interactive-ssr | 32 | neokit | 81,174.9 | 0.355 | 0.793 | 3.8 | 98.54 | 12.17 |
| interactive-ssr | 32 | next | 353.8 | 87.330 | 289.340 | 366.6 | 117.05 | 3317.04 |
| interactive-ssr | 32 | astro | 4,696.3 | 6.420 | 12.860 | 318.8 | 130.65 | 278.92 |

| Framework | Socket readiness ms | First HTTP readiness ms | Small HTML bytes | Large HTML bytes | Interactive HTML bytes |
|---|---:|---:|---:|---:|---:|
| neokit | 10.10 | 10.55 | 967 | 104,965 | 6,051 |
| next | 233.72 | 377.11 | 6,786 | 410,432 | 26,082 |
| astro | 205.16 | 220.90 | 1,750 | 105,549 | 7,193 |

## Scope and limits

Every trial validates rendered cells, checksum, UTF-8/escaping, changed query tokens and fresh timestamps before and after timing. Socket errors and non-success responses fail the run. Workloads use 3, 1000 and 50 rows. Browser asset loading, hydration, database writes, auth, distributed caches, build speed, Linux performance and full feature parity are not measured by this matrix.

HTML wire sizes differ: Next includes App Router/RSC serialized data and bootstrap, while Astro and NeoKit use HTML with small widget modules. Those architectural costs are included. CPU comes from process-group CPU-time deltas using ps; 10 ms clock resolution limits low-CPU accuracy. Sampling RSS at 250 ms can miss brief peaks. Already-exited child CPU is not included. CPU can exceed 100% because Node/V8 background threads can use multiple cores despite one server process. Pre-trial semantic validation touches all three routes, so RSS includes a warmed fixture rather than an isolated cold small-page route. Startup means fresh process readiness with warm OS caches, not a cold machine.

All servers and the client share this busy Mac, with no affinity control, controlled thermals or formal confidence intervals. Tests are interleaved to reduce ordering bias, not eliminate it. Do not extrapolate these local fixtures into a universal framework ranking. Version numbers identify the actual installed packages; this is not an assertion that all are the latest releases. [Raw samples and binary/source identities](results.json), [summary JSON](summary.json), [throughput/resource CSV](samples.csv), [fixed-rate CSV](fixed-rate.csv), and [actual-browser checks](browser-checks.json) accompany the report. [The prepared-library authentication supplement](PREPARED.md) separately checks the compiler fix against a saved prepared binary and the current monolithic build.

[Additional saved/current native regression check](REGRESSION.md) and [compiler/functional validation](VALIDATION.md) accompany this run.
