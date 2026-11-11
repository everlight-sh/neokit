# NeoKit showcase performance

3 shuffled 3-second release trials after one second warmup per case. One native worker, one wrk thread, identity encoding, no response caching. Host: Apple M1 Pro. All table figures are medians across the trials, including sampled per-trial peak RSS. Actual HTML rendering for the three examples; this is not a Next/Astro feature comparison.

| App / page | Connections | Median req/s | Client p50 ms | Client p99 ms | Sampled peak RSS MiB | CPU µs/req | CPU % of one core | HTML bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| blog `/` | 1 | 40,711 | 0.020 | 0.077 | 3.5 | 10.86 | 44.0% | 4,669 |
| blog `/` | 32 | 101,005 | 0.277 | 0.645 | 3.3 | 9.77 | 98.4% | 4,669 |
| blog `/journal/a-smaller-web` | 1 | 35,175 | 0.026 | 0.072 | 4.2 | 12.47 | 43.1% | 4,713 |
| blog `/journal/a-smaller-web` | 32 | 83,260 | 0.389 | 0.795 | 3.2 | 11.93 | 99.1% | 4,713 |
| saas `/` | 1 | 22,682 | 0.040 | 0.092 | 3.6 | 27.88 | 63.1% | 18,243 |
| saas `/` | 32 | 36,863 | 0.838 | 1.700 | 2.8 | 26.86 | 98.7% | 18,243 |
| saas `/customers?records=10000` | 1 | 12,690 | 0.074 | 0.358 | 3.5 | 60.01 | 75.9% | 13,438 |
| saas `/customers?records=10000` | 32 | 17,586 | 1.750 | 3.560 | 3.8 | 56.68 | 99.4% | 13,438 |
| ssr-site `/` | 1 | 40,733 | 0.022 | 0.093 | 4.5 | 9.35 | 38.0% | 3,416 |
| ssr-site `/` | 32 | 102,049 | 0.197 | 0.680 | 3.7 | 9.20 | 93.6% | 3,416 |

The SaaS directory filters/paginates 10,000 generated accounts and emits 25 rows. Charts/metrics are computed by native loaders. Blog content is compiled authored data, not database work. All pages include opt-in measured Server-Timing, whose header-encoding overhead is included in client timing. Browser badges report one buffered handler render, not total page loading or this saturated benchmark.

All trials validate status/body/timing before and after and reject wrk errors. Assets, font/image loading and browser rendering are excluded from this server matrix; actual-browser checks cover the complete pages separately. RSS is sampled every 250 ms; CPU is process-group time from ps. Other work remains active on this shared Mac, without affinity or thermal isolation. Local measurements are not production guarantees. [Raw samples and executable hashes](results.json), [actual-browser proof](browser-checks.json), [browser captures](screenshots/).

The raw set retains every trial, including its highest observed p99 of 78.79 ms for ssr-site `/` at C1. Its cause was not isolated. Median figures do not erase that observation. [Validation, bundle sizes and reproduction](VALIDATION.md) accompany the results.
