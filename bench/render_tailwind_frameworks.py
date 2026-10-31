#!/usr/bin/env python3
"""Render retained Tailwind build/SSR/browser observations without filtering."""
import argparse,json,statistics
from pathlib import Path
ENGINES=('Axon','Astro','Next')
SCENARIOS=('small-ssr','large-ssr','interactive-ssr')


def load(path):return json.loads(path.read_text())
def rows(path):return [json.loads(line) for line in path.read_text().splitlines()]
def median(items,key):return statistics.median(r[key] for r in items)
def kib(value):return value/1024


def render(folder):
    done=load(folder/'complete.json');assert done['complete'] and done['source_hashes_unchanged']
    builds=rows(folder/'build-samples.jsonl');samples=rows(folder/'samples.jsonl')
    meta=load(folder/'metadata.json');payload=load(folder/'payloads.json');foot=load(folder/'footprints.json')
    host=load(folder/'host.json');prep=load(folder/'preparation.json')
    assert all(not any(row['errors'].values()) for row in samples)
    browser=rows(folder/'browser-samples.jsonl') if (folder/'browser-samples.jsonl').exists() else []
    # Browser-generated fallback favicon requests differ by framework's 404 body;
    # retain them in raw samples, but exclude them from application-page bytes.
    for row in browser:
        row['assets']=[asset for asset in row['assets'] if asset['url']!='/favicon.ico']
        row['resource_encoded_bytes']=sum(a['encoded'] for a in row['assets'])
        row['resource_decoded_bytes']=sum(a['decoded'] for a in row['assets'])
        assert all(a['transfer']>0 and a['decoded']>0 for a in row['assets']), 'Cached or missing application resource'

    versions=meta['versions'];settings=meta['settings']
    s=['# Tailwind: Axon Web vs Astro vs Next.js','',
       f'Recorded {meta["started_utc"]}. {host.get("cpu",meta["platform"]["machine"])}; native Linux ARM64 Docker, 4 CPUs / 4 GiB with container swap disabled. Node {meta["node"]}, Bun {meta["bun"]}. Tailwind {versions["tailwindcss"]}; Astro {versions["astro"]}; Next.js {versions["next"]}; React {versions["react"]}. Host swap/scheduling may affect short samples.', '',
       'The same 3-row, 1,000-row and interactive 50-row catalog, same generated data/escaping/checksum and same Tailwind design. Axon uses its native CLI integration; Astro uses the official Tailwind Vite plugin; Next App Router uses Tailwind PostCSS and React useState. Default framework SSR/streaming and hydration are retained. Browser probes use request-time SSR. Static output is built and checked separately.', '',
       '## Production builds','', '| Framework | Clean SSR s | Warm SSR s | Clean static s | Warm static s |', '|---|---:|---:|---:|---:|']
    for engine in ENGINES:
        cells=[]
        for mode in ('ssr','static'):
            for kind in ('cold','warm'):cells.append(f'{median([r for r in builds if r["engine"]==engine and r["mode"]==mode and r["kind"]==kind],"elapsed_ns")/1e9:.3f}')
        s.append('| '+engine+' | '+' | '.join(cells)+' |')
    s+=['',f'{len(builds)} timed builds ({settings["build_rounds"]} shuffled cold/warm rounds). Dependency installs and checked framework-library preparation are outside timing, for all frameworks. Cold deletes project outputs and application caches; prepared Axon dependencies and installed JS packages remain. Axon preparation took {prep["framework_library_prepare_wall_s"]:.3f}s separately, including Docker exec overhead; application code is always checked and compiled. This is not a fully empty toolchain/install comparison.', '',
        '## Complete page payload','', '| Route | Framework | HTML KiB | HTML gzip KiB¹ | External CSS KiB | Inline JS KiB² |', '|---|---|---:|---:|---:|---:|']
    for scenario in SCENARIOS:
        for engine in ENGINES:
            p=payload[engine][scenario];css=sum(v['bytes'] for v in p['css_assets'].values())
            s.append(f'| {scenario} | {engine} | {kib(p["html_bytes"]):.2f} | {kib(p["html_gzip_bytes"]):.2f} | {kib(css):.2f} | {kib(p["inline_script_bytes"]):.2f} |')
    s+=['','¹ Offline gzip estimate, not a claim the server compresses HTML. ² Inline JS is already part of HTML and is not added twice. Next includes the default RSC payload. External JS resource totals below come from the real browser, including transitive/dynamically discovered chunks; the old direct-HTML-resource counts are deliberately not used for total page size. Inline CSS, when used by a framework, is already part of HTML.','']
    if browser:
        assert all(r['visibility']=='visible' and r['fcp_ms'] is not None and r['load_ms']>0 for r in browser)
        assert len(browser)==27,(len(browser),'Expected 3 routes × 3 frameworks × 3 rounds')
        s+=['## Browser cold navigation','',
            'Measured in the visible in-app browser on the host, against the Linux production servers through local Docker port forwarding. Each trial uses a new origin/port, preventing resource-cache reuse. No throttling, external fonts, CDN or WAN latency. Three rounds per route/framework; browser viewport/user agent, per-resource encoded/decoded bytes and timings are retained. The browser window changed between 1280×720 (5 trials) and 1452×1024 (22 trials); paint/load timings are exploratory and do not establish a controlled browser speed ranking. These local numbers are not internet/mobile predictions or Lighthouse/field scores.', '',
            '| Route | Framework | External + inline JS decoded KiB | Total page decoded KiB³ | Actual encoded body KiB³ | Requests⁴ |', '|---|---|---:|---:|---:|---:|']
        for scenario in SCENARIOS:
            for engine in ENGINES:
                rr=[r for r in browser if r['engine']==engine and r['scenario']==scenario];p=payload[engine][scenario]
                total=p['html_bytes']+median(rr,'resource_decoded_bytes')
                encoded=median(rr,'html_encoded_bytes')+median(rr,'resource_encoded_bytes')
                s.append(f'| {scenario} | {engine} | {kib(median(rr,"js_decoded_bytes")+p["inline_script_bytes"]):.2f} | {kib(total):.2f} | {kib(encoded):.2f} | {round(statistics.median(len(r["assets"])+1 for r in rr))} |')
        s+=['','³ Decoded total = uninstrumented production HTML + actual browser resource closure. Encoded body sum uses instrumented browser HTML plus resources and reflects each server’s default compression; it excludes headers, TLS, the common probe resource and implicit `/favicon.ico` fallback requests. Favicon requests and their differing 404 bodies remain visible in raw samples, but are excluded from application payload totals. The probe adds one deferred script/tag only with `?metrics=1`, uses Navigation/Resource/Paint Timing APIs and publishes its results into the DOM. Its own script bytes/request are excluded. The same probe is used in all apps; its overhead remains in timing. ⁴ Document + observed resources, excluding probe. Zero-byte resources/cache hits are reported and audited, not silently treated as zero download size.','',
            '| Route | Framework | TTFB ms | FCP ms | LCP ms⁵ | Load ms | Observed long tasks ms |', '|---|---|---:|---:|---:|---:|---:|']
        for scenario in SCENARIOS:
            for engine in ENGINES:
                rr=[r for r in browser if r['engine']==engine and r['scenario']==scenario]
                s.append(f'| {scenario} | {engine} | '+ ' | '.join(f'{median(rr,key):.1f} ({min(r[key] for r in rr):.1f}–{max(r[key] for r in rr):.1f})' if all(r[key] is not None for r in rr) else 'unavailable' for key in ('ttfb_ms','fcp_ms','lcp_ms','load_ms','long_tasks_ms'))+' |')
        s+=['','⁵ Last LCP candidate observed before the 1.5s post-load sample, not final field LCP. Server processes received matching HTTP warmups outside browser timing. Docker forwarding and local browser scheduling are included. Paint timing is display-frame quantized; close FCP/LCP values should be treated as ties. Long tasks are browser main-thread tasks ≥50ms.', '',
            '| Interactive counter | Click to second animation frame ms⁶ | Successful count |', '|---|---:|---:|']
        for engine in ENGINES:
            rr=[r for r in browser if r['engine']==engine and r['scenario']=='interactive-ssr']
            assert all(r.get('counter_value')=='1' and r.get('reset_value')=='0' for r in rr)
            s.append(f'| {engine} | {median(rr,"click_to_second_frame_ms"):.1f} | {len(rr)}/{len(rr)} increment + reset |')
        s+=['','⁶ Capture-phase click timestamp to the second requestAnimationFrame callback, with the new counter value checked. This is a simple visual-response check, not INP. All 27 browser navigation records are retained in `browser-samples.jsonl`; native HTTP/status checks are retained separately. The implicit missing favicon request is not counted as an application failure; functional increment/reset checks passed in all apps.','']
    else:s+=['Browser measurements pending. HTTP byte counts above are complete responses, not browser navigation timings.','']
    s+=['## Native SSR load','', '| Route | Framework | Req/s (median, range) | CPU µs/req | Peak PSS MiB | p95 ms |', '|---|---|---:|---:|---:|---:|']
    for scenario in SCENARIOS:
        for engine in ENGINES:
            rr=[r for r in samples if r['engine']==engine and r['scenario']==scenario]
            s.append(f'| {scenario} | {engine} | {median(rr,"rps"):,.0f} ({min(r["rps"] for r in rr):,.0f}–{max(r["rps"] for r in rr):,.0f}) | {median(rr,"cpu_us_per_request"):.2f} | {median(rr,"peak_pss")/2**20:.2f} | {median(rr,"p95_us")/1000:.2f} |')
    s+=['',f'{len(samples)} accepted trials, 32 keepalive connections, 1s warmup + 3s load, three interleaved rounds; server CPU 0, wrk CPU 1, sampler CPUs 2–3. Zero accepted socket/status/timeout errors. Identity encoding. Closed-loop latency can understate overload tails; throughput is complete-response throughput. No physical-energy claims.', '',
        '| Deployment | App files MiB | Separate runtime MiB | Static export KiB |', '|---|---:|---:|---:|']
    for engine in ENGINES:
        f=foot[engine];s.append(f'| {engine} | {f["app"]["bytes"]/2**20:.2f} | {f["runtime_bytes"]/2**20:.2f} | {kib(f["static"]["bytes"]):.1f} |')
    s+=['', 'Node runtime size excludes OS shared libraries. Axon embeds CSS/JS and is a single Linux ELF with no interpreter, shared libraries or undefined symbols; see `native-audit.json`. Production artifacts are verified with original apps/dependency directories renamed out of reach. The independent HTML oracle validates every row/escaping/checksum and changed query/fresh timestamps, not framework boilerplate. Static exports are checked against the same default route contents.', '',
        'This is a small matched workload, not framework feature parity or universal production capacity. The prototype lacks many Astro/Next features. Local build/SSR tests and browser data measure different machines/processes and are labeled separately. Raw samples, source snapshots/hashes, frozen dependencies, tool image, host metadata, workload source and browser probe accompany the report.', '',
        '[Reproduction](../../frameworks/TAILWIND.md). Official integration references: [Astro Vite](https://docs.astro.build/en/guides/styling/#tailwind), [Next PostCSS](https://tailwindcss.com/docs/installation/framework-guides/nextjs).','']
    (folder/'REPORT.md').write_text('\n'.join(s));print(folder/'REPORT.md')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);render(p.parse_args().folder)
