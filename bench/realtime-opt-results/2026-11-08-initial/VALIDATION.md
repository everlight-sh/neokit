# Initial optimization measurements

This directory preserves the initial prepared-frame optimization measurements,
before the empty-scheduler bypass and dispatch-time wake validation fix. It is
not the final regression certification. All raw performance trials are retained.

- `parity/`: 36 shuffled SSE trials against saved NeoKit, Next.js and Astro.
  At 1,000 subscribers, CPU fell from 6.536% to 3.770% of one core, while median
  delivery changed from 9.775 to 10.058 ms and p99 from 19.843 to 21.138 ms.
  Serial publication completion changed from 91.122 to 86.910/s. These results
  support lower CPU cost, not an improvement in end-to-end latency.
- `showcases/`: 27 SSR/concurrent-live showcase trials. A separate five-round
  simple-site follow-up (`site-followup.json`) measured a 2.1% throughput deficit.
  A later three-way diagnostic (`site-gate.json`) reversed the ordering, showing
  substantial shared-host variation; no phase is discarded.
- Browser/protocol/real-PostgreSQL checks were performed during this phase.
  Their artifacts remain here. A later wider Linux prepared-response peer found
  early close under callback work that crossed its next-wake timestamp. The
  final implementation fixes that issue and adds a deliberate delayed-wake test.

[Final source, repeated benchmarks and regression evidence](../2026-11-08/REPORT.md)
identify the binaries actually left running. Older preliminary validation prose
was replaced with these phase-specific notes to avoid implying that this initial
build passed the later expanded regression suite.
