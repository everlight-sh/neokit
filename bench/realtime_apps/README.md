# Realtime comparison fixtures

`hub.mjs` is the bounded SSE application shared by the production Next and Astro
comparison endpoints. It uses a single process-owned channel, 128 retained
events, 4,096-byte JSON payload bounds, a fixed cookie check, stream backpressure,
replay, heartbeats/renewal and explicit disconnect cleanup. Publications wake
subscribers immediately; no event polling or Redis is involved.

`../realtime_parity.py --prepare` creates isolated application source directories
under `build/realtime-parity`, links the already installed package directories,
and builds through Bun. It preserves the existing SSR comparison suites. Timed
servers run production Next/Node and Astro's standalone Node adapter. NeoKit
uses `build/push/fixture/server`, built from `tests/realtime_fixture`.

The independent Python peer verifies every event, measures external POST-to-SSE
delivery and server process-group resources, and closes every subscription.
The one-second serial ceiling includes the peer's processing and is not an
open-loop server capacity measurement.

[Measured comparison and scope](../realtime-parity-results/2026-11-08/REPORT.md),
[validation and reproduction](../realtime-parity-results/2026-11-08/VALIDATION.md).
