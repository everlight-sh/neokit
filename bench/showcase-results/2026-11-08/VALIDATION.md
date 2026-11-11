# Showcase validation

Validated on the local Apple M1 Pro Mac, 2026-11-08. All three apps build in
release mode with prepared libraries and export without credentials or services.
All content, companies and financial/account data are fictional.

- 21 Python compiler tests passed; [log](compiler-tests.log).
- 96 Bun browser-module tests passed, including existing streaming behavior;
  [log](browser-unit-tests.log).
- Independent native HTTP checks passed: fresh request timestamps, measured
  timing headers and Content-Length, HEAD, blog filtering/search/escaping/404,
  independently calculated revenue series, 10,000-account pagination through
  page 400, combined filters, CSV values and activity; [log](http-tests.log).
- Native foundation fixture returns 42 after checking byte-exact concatenation
  with empty/UTF-8/NUL parts, unchanged input, exact timing format, unchanged
  binary body, empty async placeholder, reversed timestamps, escaped chart labels
  and rejection of an invalid percentage. Source: `tests/showcase_foundations.neo`.
- Actual browser checks cover native SSR and static exports, links, search,
  computed ranges, keyboard chart exploration, chart data disclosure, pagination,
  reading progress and static-only control handling. No observed console warnings,
  errors or broken images. [Detailed record](browser-checks.json).
- Responsive checks at 390 px cover all three homepages and the 10,000-account
  directory. The document fits the viewport; the table scrolls inside its container.
  [Screenshots](screenshots/) include desktop, mobile and static views.
- Three native executables have no undefined symbols according to `nm -u`;
  [audit](native-audit.json) also records the existing macOS libSystem load command.
- 30 shuffled benchmark trials passed all status/body/timing and wrk error checks;
  [progress log](benchmark.log), [raw data](results.json).

| App | Total minified browser JavaScript, uncompressed |
|---|---:|
| Forma SSR site | 706 bytes |
| Fieldnotes blog | 1,209 bytes |
| Meridian dashboard | 1,396 bytes |

Those totals include the 31-byte framework entry stub as well as the app bundle.
They are not HTML/CSS/image sizes. [Asset identities](bundle-sizes.json),
[source/manifest hashes](source-identities.json), and executable hashes in the
benchmark data identify the measured work.

From the NeoKit root, with sibling `axon` and `axon-libs` checkouts and the Neo
Python/LLVM toolchain and Bun dependencies installed:

```sh
python3 examples/run.py
# Builds and serves at 8111–8113; Ctrl-C stops these native previews.

./neokit export --app examples/ssr-site --out build/showcase-export/ssr-site --dest build/showcase-static/ssr-site
./neokit export --app examples/blog --out build/showcase-export/blog --dest build/showcase-static/blog
./neokit export --app examples/saas --out build/showcase-export/saas --dest build/showcase-static/saas

env PYTHONPATH=. python3 tests/showcase_smoke.py
python3 -m unittest discover -s tests -p 'test_*.py'
bun test client
env PYTHONPATH=../axon python3 -m neoc build tests/showcase_foundations.neo -o build/showcase/foundations --opt-level 3 --package-root .
build/showcase/foundations
python3 bench/showcases.py --out bench/showcase-results/local
```

No benchmark builds or browser checks ran inside the timed trials. The three
user-facing previews were idle. CPU is server process-group time divided by
completed requests, excluding the load generator; process-clock resolution and
RSS sampling limit precision. The server-only matrix measures native generated
data/content rendering, not database/network waits or browser loading.

The earlier [matched NeoKit/Next/Astro benchmark](../../core-results/2026-11-08-auth-fixed/REPORT.md)
is a separate workload. Its results cannot be substituted for measurements of
these richer showcase pages. This change enables no Redis service and leaves
timing instrumentation disabled by default for other applications.
