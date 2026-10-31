# Tailwind production comparison

This follow-up uses the same catalog/counter data in Axon Web, Astro and Next.js,
with Tailwind 4.3.3. Astro uses the official Vite plugin, Next App Router uses
PostCSS, and Axon Web uses its optional native asset-build integration. Dependencies
are frozen in the Bun lockfiles. Plain-CSS fixtures in this checkout stay unchanged:
`prepare_tailwind.py` generates the Tailwind variants inside an isolated container.

Run from the Axon Web root (Docker Desktop context `desktop-linux` by default):

```sh
python3 bench/frameworks/run_tailwind_local.py \
  --out build/tailwind-frameworks/rerun --keep-container
```

The tools image is reused if present; otherwise the framework tools Dockerfile
builds it. Source/dependency preparation and `./web prepare` happen outside timed
project builds. Three rounds each: cold/warm SSR + static builds, then production
SSR at 32 connections on three routes. Reports record preparation separately.
Failed runs are retained as evidence and their own container is removed; only a
successful `--keep-container` run leaves a container for browser checks.

The output `container.json` identifies the retained container. To serve the three
production deployments for actual-browser testing:

```sh
docker --context desktop-linux exec CONTAINER \
  python3 bench/frameworks/browser_servers.py --base-port 8121
```

Axon, Astro and Next listen on ports 8121, 8122 and 8123 respectively. Add
`?metrics=1` to `/`, `/large` or `/interactive` to enable the common external
browser probe. It uses Navigation/Resource/Paint Timing and publishes JSON into
`output#browser-metrics` after 1.5s following load. Read that DOM element with the
browser tools; keep the measured tab visible. For interactive pages, use the real
increment/reset buttons and retain the updated probe JSON. This captures resources
loaded indirectly during hydration and lazy widget mounting.

Each trial uses a new port/origin for cold browser caches. Stop the browser-server
controller (SIGINT to its PID in `/tmp/axon-web-browser-servers.pid`) before the
next batch, then use base ports 8124, 8127, ... 8145. These loopback-only ports are
published by the runner. Use three rounds per route/framework (27 navigations),
Use `http://localhost:PORT` for accepted navigations, with any unmeasured browser warmups on the separate `127.0.0.1` origins. Prewarm each matching server route with one HTTP request outside browser timing (`bench/tailwind_browser_batch.py` does this). Record engine/scenario/round/URL and probe JSON in `run/browser-samples.jsonl`, and
retain screenshots of the three counters. The common instrumentation resource
is excluded from payload totals, but remains part of measured load timing.

Render retained data, without running benchmarks again:

```sh
python3 bench/render_tailwind_frameworks.py build/tailwind-frameworks/rerun/run
```

Clean up only the retained container once browser checking is finished:

```sh
docker --context desktop-linux rm -f CONTAINER
```

These are native Linux builds/servers inside Docker, with browser execution on
the local host over port forwarding. No WAN/network/device throttling is applied.
Build caches, byte accounting, paint timing limits and feature scope are described
in the generated report. Gzip estimates are separate from actual browser-encoded
bytes; the server frameworks retain their default compression behavior.

The recorded browser window varied between 1280×720 and 1452×1024; retained browser timings are exploratory. Payload accounting excludes the common probe and implicit missing-favicon fallback bodies (kept in raw resource lists). All application resources had nonzero encoded/decoded bytes and transfer sizes, ruling out resource cache hits in accepted samples.
