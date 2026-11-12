# NeoKit showcases

Three complete applications, built with native Neo SSR, typed templates, plain
CSS and small optional browser modules. They require no database, credentials,
external APIs, web fonts or charting dependencies.

| Example | Open | What it demonstrates |
|---|---|---|
| [Forma](ssr-site/README.md) | `http://127.0.0.1:8111` | Architecture studio: complete SSR documents, project illustrations, a studio page and fresh request timestamps |
| [Fieldnotes](blog/README.md) | `http://127.0.0.1:8112` | Editorial blog: complete articles, dynamic permalinks, server-side search/category filters and reading progress |
| [Meridian](saas/README.md) | `http://127.0.0.1:8113` | SaaS dashboard: live SSE metrics/chart/activity, native SVG charts, date ranges, computed KPIs, 1,000/10,000 demo accounts, bounded pagination, search, plan filters, CSV and JSON endpoints |

From the NeoKit root:

```sh
bun install --frozen-lockfile
python3 examples/run.py
# Ctrl-C stops the three native servers.
```

Use `python3 examples/run.py --no-build` after building, or select another trio
of ports with `--base-port 8211`. Python supervises local previews; each server
is a standalone compiled Neo executable. The existing `studio`, authenticated
task `dashboard`, and `features` examples remain available.

Each showcase enables `[server] timing=true`. The badge reads this response's
`Server-Timing: render` metric through the browser Performance API. It measures
buffered handler/loader/template work through response encoding, excluding the
timing header itself, socket delivery, styles, images and browser work. The
badge's tooltip separately shows navigation TTFB. Unsupported browsers show an
unquantified SSR label. Static exports say that no server is needed. Figures are
measured per request, rather than embedded promotional numbers.

All companies, publication authors, customer accounts and financial data are
fictional. Meridian is a public read-only demo; use the separate authenticated
`dashboard` example for signed sessions, CSRF and persistent CRUD.

Measured performance and actual-browser checks of these examples (release
builds, response timing, throughput, sampled process resources and the limits
of local measurements) are in the separate `neokit-bench` repository. Check the
showcases with:

```sh
env PYTHONPATH=. python3 tests/showcase_smoke.py
```

Reusable foundations live below the apps: Neo's `str_concat_parts` and
`web_server_timing`, plus NeoKit's `src/charts.neo` and browser timing widget.
[Their APIs and limits](../docs/charts.md) are documented for reuse. No new
functionality is hidden in the example-specific build process.
