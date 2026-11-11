# Meridian — a SaaS analytics dashboard

A responsive public demo with overview, customer directory and activity pages.
Data is deterministic and synthetic, computed by native Neo loaders on each
request. There is no analytics service, database or authentication setup.

The overview also has a **Live workspace**: server-published demo invoices,
signups and plan upgrades update revenue/signups, a rolling chart and an activity
feed without reloading. Pause/resume reconnects with a cursor. All active tabs
share the same events and totals. A separate native producer runs every two seconds, including while no browser
is subscribed. Publishing wakes subscribers through kernel notifications, with
no subscriber polling and no Redis. Its
ephemeral counters are separate from the seeded historical dataset.
[Realtime API and measured performance](../../docs/realtime.md).

```sh
./neokit serve --app examples/saas --out build/showcase/saas --port 8113
./neokit export --app examples/saas --out build/showcase-export/saas --dest build/showcase-static/saas
./neokit preview --dir build/showcase-static/saas --port 8213
```

- Overview: select 7, 30 or 90 days and a 1,000 or 10,000-account dataset. Revenue,
  previous-period comparison, MRR, active/trial counts, SVG paths and dates are
  computed on the server. Chart points support hover/keyboard focus. The native
  data table under the chart works without JavaScript.
- Customers: `/customers?records=10000` keeps 25 rows in the HTML/DOM. Search and
  plan filters run on the server, and pagination preserves them. No 10,000-row
  browser table or client-side copy of the account dataset is created.
- CSV: `/api/export-customers?records=10000&plan=Pro&q=Orbit` exports matching
  demo accounts. `/api/metrics?range=7&records=10000` returns computed JSON and a
  fresh request timestamp.
- Activity: a seeded event feed demonstrates a second dashboard page.

All payment figures and accounts are fictional. Read-only public data is used to
make the example immediately runnable. The separate `examples/dashboard` app
demonstrates real authentication, CSRF checks and durable task mutations.

Static export is an interactive chart snapshot. It omits CSV/API endpoints and
hides server-only search, filtering and pagination controls. It does not pretend
to execute backend operations on a static host.

The browser bundle adds chart exploration, current-navigation styling and a
measured server-timing badge. The application uses native GET navigation and
forms, so full pages remain useful before browser code runs. Reusable chart
helpers are in `src/charts.neo`: escaped labels, bounded numeric input, native
SVG and accessible data tables; no chart package or browser rendering engine
runs on the server.
