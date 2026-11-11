# Compiler fix validation

Reusable fixes live in the Neo compiler (`../axon`); no application hot path was replaced for this run.

- Retain checked return-alias contracts on a prechecked library hit.
- Cache integrity-checked pre-optimization dependency IR alongside the native object.
- Apply the source-size and constant-argument inlining policy inside libraries and when importing application hints, including effectful allocator helpers. Preserve cold decoder `noinline` annotations.
- Keep cached objects and hints free of application bodies; application checking still runs.

Validation completed:

- `cd ../axon && python3 tests/native_library_cache.py`: PASS. Includes dependency/source/type invalidation, corrupted objects and hints, application proof/effect rejection, managed return/alias ownership, cold decoder compatibility, and deterministic cold/warm binaries.
- `PYTHONPATH=. python3 -c 'from tests.dashboard_smoke import main; main("build/cache-fixed/dashboard/server")'`: PASS. Real 600,000-round password login, logout, CSRF, HTML escaping, cross-user authorization, 24 concurrent durable writes across two workers, and restart persistence.
- Removed only the generated dashboard application build cache, retained the prepared library cache, then rebuilt: identical application IR and binary SHA-256 for cold checking and prechecked reuse. The warm build reused 1,320 checked obligations and 241 embedded library runtime checks.
- Actual browser checks: NeoKit, Next and Astro each incremented 0 → 3 and reset to 0. See [browser evidence](browser-checks.json) and [screenshot](browser-verified.png).
- Return-alias and inlining unit tests: PASS, 11 tests.
- All 88 timed trials passed semantic checks and recorded binary identities remained unchanged. The [additional saved/current check](REGRESSION.md) separately checks small-page regression under the noisy host.
- No compiler builds or other benchmark jobs ran concurrently with the timed suites. Other user applications remained running; host noise is a limitation.

Reproduction after building the named binaries:

```sh
python3 bench/prepared_stats.py \
  --out bench/core-results/2026-11-07-prepared-fixed \
  --before build/performance/prepared-gap-before/dashboard-prepared \
  --prepared build/cache-fixed/dashboard/server \
  --monolithic build/dashboard/server
python3 bench/core_stats.py \
  --out bench/core-results/2026-11-07-prepared-fixed \
  --neokit-binary build/cache-fixed/catalog/server
```

Use a fresh output directory to preserve this run. [Compiler source hashes and warm-build identity](compiler-validation.json) identify the validated implementation. These checks validate the prepared compiler fix and local fixtures; they do not establish full framework parity or production readiness of earlier experimental async/shutdown work.
