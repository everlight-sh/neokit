# Production framework benchmark

Matched Axon Web, Astro, and Next.js examples, with actual production servers and static exports. This is a catalog/counter workload, not a claim of feature parity or a universal framework ranking.

## Run

Requires Docker with at least four CPUs and 4 GiB RAM available, Python 3, at least 4 GiB spare disk, and the external Axon compiler checkout. The default Docker context is `desktop-linux`; pass `--context default` on a typical Linux host. No host JavaScript dependencies are installed.

From the Axon Web repository:

```sh
python3 bench/frameworks/test_harness.py
python3 bench/frameworks/run_local.py \
  --axon-home ../axon \
  --out build/framework-results/local-run
```

The script builds a tools image, copies clean sources into an isolated container, installs the frozen Bun lockfile, builds and measures production artifacts, writes a report, and removes its own container. Dependencies/image installation is outside timed builds. No global Docker cache pruning occurs. To reuse the tools image, pass `--skip-image-build`. Use a fresh output directory each time.

Default settings: 5 runtime rounds, 4 seconds per sample, 1 second warmup; 3 cold/warm build rounds for SSR and static output. Adjust with `--rounds`, `--duration`, and `--build-rounds`. Runtime rounds must be at least three. A full run takes several minutes. Results contain `run/REPORT.md`, every raw sample/log, source snapshots/hashes, exact versions, validation, deployment/payload sizes, and an Axon ELF dependency audit. Regenerate only the report with:

```sh
python3 bench/frameworks/render_report.py build/framework-results/local-run/run
```

Pinned framework versions and all transitive dependencies are in `package.json` and `bun.lock`. Node 24 runs the frameworks' production builds and servers; Bun manages dependencies and bundles Axon browser modules. Python/LLVM build the native Axon executable. Tools image identity and actual Node/Bun versions are recorded. The Dockerfile uses a Node major tag and Ubuntu package repositories: a future rebuilt tools image may contain updated tool versions, so compare the recorded identities. This is a native architecture test, not QEMU emulation.

## Workloads

| Path | Default rows | Behavior |
|---|---:|---|
| `/` | 3 | Small request-time SSR page |
| `/large` | 1,000 | Larger request-time SSR table |
| `/interactive` | 50 | SSR table with increment/reset counter |

All apps compute the same deterministic rows and checksum, escape the same Unicode/special-character text, and accept `n`, `seed`, and `token` query parameters. Default seed is 17. Server timestamps and `Cache-Control: no-store` prove request-time rendering. `model.py` is an independent Python HTML oracle; it compares every table cell, metadata, token, checksum and counter presence. It exercises changed inputs and verifies fresh timestamps before timing. It rejects malformed content in `test_harness.py`. Browser verification additionally checks the three increment/reset implementations.

`prepare_sources.py` regenerates matched route fixtures. The generated apps are included for inspection; running regeneration is unnecessary for normal benchmarks. Each framework uses its own renderer:

- Axon: ordinary typed Axon functions and native templates; balanced string concatenation; assets embedded in its single executable.
- Astro: `.astro` components, server output, official standalone Node adapter, default streaming. Sessions disabled because this app does not use them. Vanilla JavaScript counter, no React integration.
- Next.js: App Router React server components, `connection()` for live rendering, `searchParams`, `useState` client counter; standalone deployment and default Turbopack builds. A separate static app imports the same shared components with static props.

Default production framework behavior is retained, including Next's RSC/bootstrap scripts. No synthetic extra work is inserted to equalize differing HTML byte counts. No implementation serves precomputed HTML for the SSR workload. Static exports use the same route contents with a zero timestamp and fixture token.

## Measurement

- Server pinned to CPU 0; single-threaded `wrk` pinned to CPU 1; measurement driver/sampler on CPUs 2–3. Builds use CPUs 0–3. One server process/worker, no load balancer. Container memory limited to 4 GiB with no swap.
- HTTP/1.1 connections 1 and 32. Identity encoding; localhost inside the same Linux VM. One server at a time; a fresh process per trial, with warmup and settling before measurement. Fixed seeded shuffled order, all samples retained.
- `wrk` uses a benchmark-only `CLOCK_MONOTONIC` preload for its `gettimeofday` calls, avoiding wall-clock corrections in latency subtraction. The tiny C tool shim is applied only to the load generator; Axon servers remain unchanged, C-free and dependency-free. Its source is included and hashed.
- `wrk` reports complete-response throughput and p50/p95/p99; serial Python probes additionally retain TTFB/full times. Closed-loop latency can understate overload tails (coordinated omission). Single-threaded client CPU cost and TCP backpressure can bound fast native throughput; load CPU time is retained.
- CPU is aggregate process-group CPU time across the load interval. RSS/PSS samples every 50 ms count processes once, not thread RSS. PSS apportions shared memory. Transient peaks and work at interval boundaries are limitations. Neither is a physical energy measurement.
- Startup is process launch to accepting TCP connections, checked every 10 ms; it is not time to a complete first request. No OS page-cache flushing is performed.
- Cold builds remove project build outputs/caches; installed dependencies and OS caches remain warm. Warm rebuild follows each cold build. The original recorded Axon CLI built both server and snapshot exporter. The updated CLI selects the needed target; see the [build-speed follow-up](../build-speed-results/2026-10-26/REPORT.md). Dependency installation is not included.
- Runtime artifacts are copied into deployment directories. Original apps and node_modules are renamed out of reach for runtime validation and measurements and restored in `finally`. Astro's closure is copied using Next's bundled `nodeFileTrace`; Next uses official standalone output. Tracing is packaging, outside timed builds.
- Axon's ELF must have no interpreter, NEEDED shared libraries, or undefined symbols. The Node executable is reported separately from JS/dependency output; OS shared libraries are not included in that number.
- HTML-referenced JS/preload bytes and inline scripts are measured from real responses. Transitive imports and dynamically discovered chunks are excluded: this is not a browser network/INP benchmark. All emitted static JS is reported separately. Gzip sizes are offline calculations; SSR load tests are uncompressed.
- Static export correctness/build time/size are measured. Static file serving throughput is intentionally excluded because it depends on hosting/CDN configuration.

The suite excludes databases, external I/O, auth, server actions, React streaming component trees, browser interaction latency and multi-worker scaling. Axon Web remains a small prototype; stronger microbenchmark results do not imply the feature coverage or operational maturity of Astro/Next.js.

Official references: [Astro Node adapter](https://docs.astro.build/en/guides/integrations-guide/node/), [Next standalone output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output), [Next connection API](https://nextjs.org/docs/app/api-reference/functions/connection).

## Recorded run

See [2026-10-26 results](results/2026-10-26/REPORT.md). It includes raw samples and source identities so the summary can be checked without rerunning the suite.

The [Tailwind follow-up](../tailwind-framework-results/2026-10-26/REPORT.md)
adds matched Tailwind 4 builds, real browser resource closure and exploratory
navigation/paint timing across 27 visible-browser navigations. See
[TAILWIND.md](TAILWIND.md) for reproduction and limitations.
