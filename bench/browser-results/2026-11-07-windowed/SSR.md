# List SSR delivery measurements

Equal logical data, different rendering strategies: full HTML versus a bounded SSR window plus inert JSON. This is a product-level delivery comparison, not equal-DOM rendering throughput.

Three shuffled 2-second trials after a 1-second warmup, one native worker and one wrk thread, identity encoding. All requests independently validate record count/data; windowed freshness is checked.

| Records | Connections | Strategy | Median req/s | Response bytes | Median p99 ms |
|---|---:|---|---:|---:|---:|
| 1000 | 1 | before | 1384.7 | 949678 | 1.100 |
| 1000 | 1 | windowed | 14452.5 | 43660 | 0.119 |
| 1000 | 32 | before | 2229.3 | 949678 | 28.020 |
| 1000 | 32 | windowed | 22308.2 | 43660 | 2.800 |
| 10000 | 1 | before | 131.9 | 9462696 | 9.480 |
| 10000 | 1 | windowed | 2394.5 | 78675 | 0.542 |
| 10000 | 32 | before | 153.3 | 9462696 | 356.820 |
| 10000 | 32 | windowed | 3403.8 | 78675 | 18.300 |
