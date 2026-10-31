#!/usr/bin/env python3
"""Render retained measurements without rerunning or filtering trials."""
import argparse,json,statistics
from pathlib import Path

ENGINES=('Axon','Astro','Next')
def median(items,key):return statistics.median(x[key] for x in items)
def load(path):return json.loads(path.read_text())
def lines(path):return [json.loads(x) for x in path.read_text().splitlines()]
def render(folder):
    meta=load(folder/'metadata.json');done=load(folder/'complete.json');assert done['complete'] and not done['self_test']
    samples=lines(folder/'samples.jsonl');builds=lines(folder/'build-samples.jsonl');foot=load(folder/'footprints.json');payload=load(folder/'payloads.json')
    settings=meta['settings'];s=['# Axon Web / Astro / Next.js production benchmark','',
    'Recorded '+meta['started_utc']+'. Native Linux '+meta['platform']['machine']+' in Docker Desktop on Apple M1 Pro; local closed-loop HTTP/1.1.',
    '',f"Versions: Astro {meta['versions']['astro']}, @astrojs/node {meta['versions']['@astrojs/node']}, Next.js {meta['versions']['next']}, React {meta['versions']['react']}; {meta['node']} / Bun {meta['bun']}.",
    '',f"{len(samples)} SSR trials: {settings['rounds']} rounds × 3 routes × 2 concurrency levels × 3 frameworks. Each sample is {settings['duration_s']}s after {settings['warmup_s']}s warmup. Production servers pinned to one CPU, wrk to another; builds use four CPUs. Container: 4 GiB RAM, no swap. Shuffled order; medians of every retained sample, no outlier removal.",
    '', '## Request throughput and latency','', 'Same request-time catalog rows, checksum, escaping and token. Fresh timestamps and changed query parameters are verified independently before load; bodies verified before and after each trial. These are warm server measurements with caching disabled, identity encoding, and framework streaming defaults retained. p95 measures complete responses.',
    '', '| Route | Connections | Framework | Median req/s (min–max) | p95 ms¹ | CPU µs/request | Peak PSS MiB¹ |', '|---|---:|---|---:|---:|---:|---:|']
    for route in ('small-ssr','large-ssr','interactive-ssr'):
        for c in (1,32):
            for e in ENGINES:
                rows=[x for x in samples if x['engine']==e and x['scenario']==route and x['connections']==c];assert len(rows)==settings['rounds']
                s.append(f"| {route} | {c} | {e} | {median(rows,'rps'):,.0f} ({min(x['rps'] for x in rows):,.0f}–{max(x['rps'] for x in rows):,.0f}) | {median(rows,'p95_us')/1000:.3f} | {median(rows,'cpu_us_per_request'):.2f} | {median(rows,'peak_pss')/2**20:.2f} |")
    s+=['','¹ Median of trial p95s / trial sampled peaks, rather than a pooled p95 or absolute lifetime peak. CPU is process-group CPU time over the load interval; GC/in-flight work near interval boundaries can affect it. PSS accounts for shared pages proportionally; 50 ms sampling can miss transient peaks.',
    '', '## Startup and deployment','', '| Framework | TCP-ready startup ms² | App / traced deps MiB | Runtime executable MiB³ | Idle PSS MiB |', '|---|---:|---:|---:|---:|']
    for e in ENGINES:
        rows=[x for x in samples if x['engine']==e]
        s.append(f"| {e} | {median(rows,'startup_ns')/1e6:.2f} | {foot[e]['app']['bytes']/2**20:.3f} | {foot[e]['runtime_bytes']/2**20:.2f} | {median(rows,'idle_pss')/2**20:.2f} |")
    s+=['','² New process to accepting TCP connections, 10 ms polling resolution, warmed filesystem cache. It is not time to first complete request. Idle PSS is measured after warmup and settling.',
    '³ Axon embeds assets and has no extra runtime executable. Node executable sizes exclude OS shared libraries. Deployment sizes are logical file bytes, not compressed image sizes. Original source apps and node_modules are renamed out of reach during verification and all runtime trials. The Axon ELF audit rejects an interpreter, NEEDED shared libraries and undefined symbols.',
    '', '## Response and client script size','', '| Route | Framework | HTML bytes | HTML gzip bytes | HTML-referenced external JS bytes | Inline JS bytes |','|---|---|---:|---:|---:|---:|']
    for route in ('small-ssr','large-ssr','interactive-ssr'):
        for e in ENGINES:
            p=payload[e][route];s.append(f"| {route} | {e} | {p['html_bytes']:,} | {p['html_gzip_bytes']:,} | {p['html_referenced_external_js_bytes']:,} | {p['inline_script_bytes']:,} |")
    s+=['','JS counts scripts/preloads referenced directly in HTML, plus inline scripts. It excludes transitive ESM imports and dynamically discovered chunks; this is **not** a browser download/INP measurement. Next includes its default RSC/hydration payload and client runtime; Astro uses its normal script pipeline; Axon uses its island client. HTML differs because default framework architecture is retained. Gzip sizes are offline calculations, not the uncompressed load-test traffic.',
    '', '## Production builds and static export','', '| Framework | Mode | Cold build s | Warm rebuild s | Cold sampled peak PSS MiB |', '|---|---|---:|---:|---:|']
    for e in ENGINES:
        for mode in ('ssr','static'):
            cold=[x for x in builds if x['engine']==e and x['mode']==mode and x['kind']=='cold'];warm=[x for x in builds if x['engine']==e and x['mode']==mode and x['kind']=='warm']
            s.append(f"| {e} | {mode} | {median(cold,'elapsed_ns')/1e9:.3f} | {median(warm,'elapsed_ns')/1e9:.3f} | {median(cold,'peak_pss')/2**20:.1f} |")
    s+=['','Cold removes project output and framework build caches, with dependencies already installed and OS filesystem caches uncontrolled. Warm is an immediate rebuild. Axon currently builds both its native server and snapshot exporter in each command, including static export. Astro uses official standalone Node output with sessions disabled (unused); Next uses standalone App Router and default Turbopack production builds.',
    '', '| Framework | Static export KiB | Emitted JS KiB | HTML KiB |', '|---|---:|---:|---:|']
    for e in ENGINES:
        f=foot[e]['static'];s.append(f"| {e} | {f['bytes']/1024:.1f} | {f['client_js_bytes']/1024:.1f} | {f['html_bytes']/1024:.1f} |")
    s+=['','Static exports are checked against the same three default page contents. No static-file throughput ranking: that primarily compares hosting configuration.',
    '', '## Interpretation and limits','',
    'This benchmark measures a small matched SSR catalog and counter, not overall framework quality. It excludes databases, external I/O, auth, concurrent streaming components, server actions, production observability, browser navigation performance and multi-worker scaling. Mature frameworks provide substantially more features than this Axon prototype. A single host and short closed-loop trials do not establish production capacity; coordinated omission can understate latency under overload. CPU time is an efficiency proxy, not an energy measurement.',
    '', 'All raw samples, logs, source snapshots, source SHA-256s, exact versions, commands, image identity, CPU metadata, payload resource lists, validation and the native dependency audit are retained beside this report. [Reproduction and methodology](../../../bench/frameworks/README.md).',
    '', 'Official configuration references: [Astro Node adapter](https://docs.astro.build/en/guides/integrations-guide/node/), [Next standalone output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output), [Next request-time connection](https://nextjs.org/docs/app/api-reference/functions/connection).','']
    (folder/'REPORT.md').write_text('\n'.join(s))
    print(folder/'REPORT.md')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);render(p.parse_args().folder)
