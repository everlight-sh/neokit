# Build-speed regression benchmarks

[Latest Axon vs Astro results](build-speed-results/2026-10-26/REPORT.md) cover production SSR/static exports, development builds, first-use library preparation, warm rebuilds, and live SSR throughput.

From the framework repository:

```sh
python3 bench/frameworks/run_local.py --build-speed \
  --axon-home ../axon --build-rounds 5 --rounds 3 \
  --out build/build-speed-rerun
```

This uses the benchmark Docker tools image, copies sources into an isolated native Linux container, installs the frozen Astro dependencies outside timing, runs both frameworks on the same four build CPUs, writes `run/REPORT.md`, and removes its own container. The default Docker context is `desktop-linux`; select your host context with `--context`. An existing tools image can be reused with `--skip-image-build`. Use a new output directory each time. No compiler/application source mount is modified by the container.

The comparison explicitly separates:

- **First use:** both Axon project and compiler-library caches are empty; dependency code is checked and compiled automatically. Three independent trials.
- **Toolchain preparation:** `./web prepare` checks and compiles only runtime/stdlib/framework code once. No page/loader/application data is included in those artifacts.
- **Clean project:** project outputs and native cache are deleted before each trial. Only prepared dependency artifacts survive, just as Astro's installed Node/framework tools survive. Axon asserts an application compile miss on every cold trial. Five shuffled cold/warm rounds.
- **Warm rebuild:** immediately repeats the same project build; measured separately.

Production application and library code uses LLVM `-O3`. Dev application code uses `-O0` with the same checks and optimized dependencies. Signatures and every application body are checked on a clean build. Dependency bodies are checked when compiled; source/compiler/flag changes invalidate their content-addressed, integrity-checked object/IR/capability metadata. Objects are linked statically into the executable, with no new deployed runtime dependencies. Tests cover application rejection, effects, imported type ABI changes, artifact corruption and identical code for cold/warm library states.

SSR builds compile only the server unless routes require prerendering; static export compiles only its renderer. The Python `build()` API retains `target="all"` for compatibility. The old pipeline compiled both executables for either command.

The independent catalog oracle checks every exported cell, Unicode escaping and checksum, plus changed live query inputs and fresh timestamps. Runtime checks use one server CPU, 32 connections, a monotonic wrk clock, 1s warmup and 3s load, interleaved across frameworks. All samples/ranges, CPU/request, sampled PSS, source snapshots/hashes, tool versions and static ELF audits are retained. These short local samples are not a production capacity or framework feature-parity claim.

The original [Astro/Next comparison](frameworks/results/2026-10-26/REPORT.md) remains historical. The latest follow-up reruns Astro rather than assigning new rankings from older numbers. An optional `--before-binary` argument on `bench/astro_build_speed.py` also compares an identified pre-change native server; the Docker wrapper normally compares the current Axon and Astro servers.

The earlier paired Axon build-only harness remains at `bench/build_speed.py`; its historical source snapshot is required for the before version.
