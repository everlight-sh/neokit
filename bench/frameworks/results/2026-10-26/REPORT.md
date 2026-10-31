# Axon Web / Astro / Next.js production benchmark

Recorded 2026-10-26T08:52:50.248780+00:00. Native Linux aarch64 in Docker; local closed-loop HTTP/1.1. Host: Apple M1 Pro.

Versions: Astro 7.4.2, @astrojs/node 11.1.9, Next.js 16.4.3, React 19.3.1; v24.22.1 / Bun 1.3.13.

90 SSR trials: 5 rounds × 3 routes × 2 concurrency levels × 3 frameworks. Each sample is 4s after 1s warmup. Production servers pinned to one CPU, wrk to another; builds use four CPUs. Container: 4 GiB RAM, no swap. Shuffled order; medians of every retained sample, no outlier removal.

## Request throughput and latency

Same request-time catalog rows, checksum, escaping and token. Fresh timestamps and changed query parameters are verified independently before load; bodies verified before and after each trial. These are warm server measurements with caching disabled, identity encoding, and framework streaming defaults retained. p95 measures complete responses.

| Route | Connections | Framework | Median req/s (min–max) | p95 ms¹ | CPU µs/request | Peak PSS MiB¹ |
|---|---:|---|---:|---:|---:|---:|
| small-ssr | 1 | Axon | 20,173 (10,935–30,973) | 0.363 | 23.96 | 4.76 |
| small-ssr | 1 | Astro | 2,485 (956–3,182) | 6.421 | 383.08 | 265.81 |
| small-ssr | 1 | Next | 372 (316–449) | 14.575 | 2658.65 | 344.82 |
| small-ssr | 32 | Axon | 66,257 (58,656–74,625) | 1.238 | 14.59 | 15.93 |
| small-ssr | 32 | Astro | 4,385 (4,005–4,729) | 27.520 | 228.37 | 287.42 |
| small-ssr | 32 | Next | 427 (317–554) | 165.020 | 2346.47 | 363.21 |
| large-ssr | 1 | Axon | 2,341 (955–2,858) | 3.911 | 362.07 | 1.50 |
| large-ssr | 1 | Astro | 359 (147–384) | 3.803 | 2753.82 | 170.36 |
| large-ssr | 1 | Next | 28 (18–33) | 66.349 | 35263.16 | 336.36 |
| large-ssr | 32 | Axon | 3,147 (2,581–3,209) | 18.253 | 317.69 | 5.25 |
| large-ssr | 32 | Astro | 383 (183–396) | 144.225 | 2610.68 | 261.98 |
| large-ssr | 32 | Next | 21 (16–29) | 2881.346 | 47619.05 | 399.30 |
| interactive-ssr | 1 | Axon | 5,749 (3,606–15,793) | 1.283 | 75.52 | 1.55 |
| interactive-ssr | 1 | Astro | 1,216 (181–2,015) | 7.035 | 783.91 | 256.59 |
| interactive-ssr | 1 | Next | 131 (87–268) | 34.174 | 7556.82 | 316.27 |
| interactive-ssr | 32 | Axon | 25,664 (18,601–31,997) | 5.018 | 38.23 | 6.87 |
| interactive-ssr | 32 | Astro | 1,950 (1,611–2,826) | 45.690 | 508.76 | 293.25 |
| interactive-ssr | 32 | Next | 150 (46–299) | 651.366 | 6677.63 | 312.43 |

¹ Median of trial p95s / trial sampled peaks, rather than a pooled p95 or absolute lifetime peak. CPU is process-group CPU time over the load interval; GC/in-flight work near interval boundaries can affect it. PSS accounts for shared pages proportionally; 50 ms sampling can miss transient peaks.

## Startup and deployment

| Framework | TCP-ready startup ms² | App / traced deps MiB | Runtime executable MiB³ | Idle PSS MiB |
|---|---:|---:|---:|---:|
| Axon | 12.59 | 0.272 | 0.00 | 1.56 |
| Astro | 213.81 | 39.431 | 117.66 | 161.62 |
| Next | 258.91 | 53.364 | 117.66 | 175.08 |

² New process to accepting TCP connections, 10 ms polling resolution, warmed filesystem cache. It is not time to first complete request. Idle PSS is measured after warmup and settling.
³ Axon embeds assets and has no extra runtime executable. Node executable sizes exclude OS shared libraries. Deployment sizes are logical file bytes, not compressed image sizes. Original source apps and node_modules are renamed out of reach during verification and all runtime trials. The Axon ELF audit rejects an interpreter, NEEDED shared libraries and undefined symbols.

## Response and client script size

| Route | Framework | HTML bytes | HTML gzip bytes | HTML-referenced external JS bytes | Inline JS bytes |
|---|---|---:|---:|---:|---:|
| small-ssr | Axon | 964 | 541 | 0 | 0 |
| small-ssr | Astro | 1,747 | 913 | 0 | 0 |
| small-ssr | Next | 7,550 | 2,344 | 569,180 | 5,835 |
| large-ssr | Axon | 104,962 | 9,765 | 0 | 0 |
| large-ssr | Astro | 105,546 | 10,280 | 0 | 0 |
| large-ssr | Next | 408,841 | 32,103 | 569,180 | 303,128 |
| interactive-ssr | Axon | 6,048 | 1,373 | 4,925 | 0 |
| interactive-ssr | Astro | 7,190 | 1,896 | 0 | 399 |
| interactive-ssr | Next | 26,751 | 4,416 | 569,180 | 20,005 |

JS counts scripts/preloads referenced directly in HTML, plus inline scripts. It excludes transitive ESM imports and dynamically discovered chunks; this is **not** a browser download/INP measurement. Next includes its default RSC/hydration payload and client runtime; Astro uses its normal script pipeline; Axon uses its island client. HTML differs because default framework architecture is retained. Gzip sizes are offline calculations, not the uncompressed load-test traffic.

## Production builds and static export

| Framework | Mode | Cold build s | Warm rebuild s | Cold sampled peak PSS MiB |
|---|---|---:|---:|---:|
| Axon | ssr | 17.603 | 0.839 | 270.2 |
| Axon | static | 18.288 | 0.839 | 271.2 |
| Astro | ssr | 0.947 | 0.742 | 481.4 |
| Astro | static | 0.931 | 0.711 | 411.3 |
| Next | ssr | 4.670 | 1.871 | 931.9 |
| Next | static | 3.987 | 1.891 | 909.6 |

Cold removes project output and framework build caches, with dependencies already installed and OS filesystem caches uncontrolled. Warm is an immediate rebuild. Axon currently builds both its native server and snapshot exporter in each command, including static export. Astro uses official standalone Node output with sessions disabled (unused); Next uses standalone App Router and default Turbopack production builds.

| Framework | Static export KiB | Emitted JS KiB | HTML KiB |
|---|---:|---:|---:|
| Axon | 116.9 | 5.4 | 109.3 |
| Astro | 111.8 | 0.0 | 111.8 |
| Next | 1817.6 | 556.7 | 455.2 |

Static exports are checked against the same three default page contents. No static-file throughput ranking: that primarily compares hosting configuration.

## Interpretation and limits

This benchmark measures a small matched SSR catalog and counter, not overall framework quality. It excludes databases, external I/O, auth, concurrent streaming components, server actions, production observability, browser navigation performance and multi-worker scaling. Mature frameworks provide substantially more features than this Axon prototype. A single host and short closed-loop trials do not establish production capacity; coordinated omission can understate latency under overload. CPU time is an efficiency proxy, not an energy measurement.

All raw samples, logs, source snapshots, source SHA-256s, exact versions, commands, image identity, CPU metadata, payload resource lists, validation and the native dependency audit are retained beside this report. [Reproduction and methodology](../../README.md).

Official configuration references: [Astro Node adapter](https://docs.astro.build/en/guides/integrations-guide/node/), [Next standalone output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output), [Next request-time connection](https://nextjs.org/docs/app/api-reference/functions/connection).

## Measurement provenance

Load-generator clock: CLOCK_MONOTONIC via benchmark-only wrk LD_PRELOAD; never applied to servers. The benchmark-only preload is applied only to wrk; it is never linked into or loaded by an Axon executable.

A preliminary run stopped on 32 wrk histogram-range/timeout errors despite a 4-second test and 10-second configured bound. wrk uses wall-clock subtraction and labels out-of-range latency samples as timeouts ([upstream source](https://github.com/wg/wrk/blob/master/src/wrk.c)). The cause was not established conclusively. A second attempt was stopped by the host disk guard. Both incomplete attempts are retained under `preliminary/`; neither contributes runtime samples to this report. Every accepted runtime trial uses monotonic client timing and retains zero socket/status/timeout errors.

Completed build trials were reused after verifying build inputs and adapters had unchanged hashes; see `original-build-metadata.json` and `metadata.json`. Only benchmark driver/report/clock instrumentation changed. Production artifacts were retained to avoid reinstalling dependencies when host disk space became tight. The final Axon binary hash matches the preliminary binary hash.

Host swap was in use during this local Docker run; short samples are subject to host scheduling and memory pressure. Ranges are retained. These results are not a dedicated-host capacity result.

