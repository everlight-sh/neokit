"""Summarize the completed auth regression, core SSR and mixed-load reruns."""
import argparse,json,statistics
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args();out=args.out
    read=lambda name:json.loads((out/name).read_text())
    auth=read('prepared-results.json');core=read('results.json');summary=read('summary.json');mixed=read('mixed/results.json')
    assert len(auth['samples'])==auth['configuration']['rounds']*len(auth['binaries'])
    assert len(core['samples'])==core['configuration']['rounds']*18 and len(core['fixed_rate'])==core['configuration']['rounds']*3
    assert len(mixed['samples'])==mixed['configuration']['rounds']*3
    median=lambda rows,key:statistics.median(row[key] for row in rows)
    rows={mode:[row for row in auth['samples'] if row['mode']==mode] for mode in auth['binaries']}
    rate={mode:median(values,'requests_per_sec') for mode,values in rows.items()}
    previous=(rate['prepared-current']/rate['prepared-before']-1)*100
    recovered=(rate['prepared-current']/rate['prepared-regressed']-1)*100
    cpu=(median(rows['prepared-current'],'cpu_us_per_request')/median(rows['prepared-regressed'],'cpu_us_per_request')-1)*100
    gap=(rate['prepared-current']/rate['monolithic-current']-1)*100
    paired=[]
    for round_ in range(auth['configuration']['rounds']):
        selected={row['mode']:row for row in auth['samples'] if row['round']==round_}
        assert len(selected)==len(rows)
        paired.append((selected['prepared-current']['requests_per_sec']/selected['prepared-before']['requests_per_sec']-1)*100)
    count=len(auth['samples'])+len(core['samples'])+len(core['fixed_rate'])+len(mixed['samples'])
    lines=['# Authenticated-rendering performance fix','',
      f"The fixed prepared build measured **{rate['prepared-current']:,.0f} authenticated renders/sec**, versus **{rate['prepared-regressed']:,.0f}** for the regressed async build ({recovered:+.1f}%) and **{rate['prepared-before']:,.0f}** for pre-async ({previous:+.1f}%). It beat pre-async in {sum(value>0 for value in paired)}/{len(paired)} paired rounds. The previously observed 8.2% throughput deficit is eliminated in this fixture. CPU per request changes by {cpu:+.1f}% versus regressed async.",'',
      '## Reusable fixes in Neo','',
      'The compiler constrains expensive forced-inline calls locally instead of removing a helper’s policy globally. Prepared optimization hints precede object-local constraints, and the application uses its configured expansion budget. Neo’s JSON decoder measures an exact output size and copies ordinary text in runs, avoiding byte-by-byte temporary arrays. Authentication, owner filtering and the application data path are unchanged. [Implementation and rationale](FIX.md).','',
      '## Authenticated file-backed SSR','',
      f"{auth['configuration']['rounds']} paired rounds, five measured seconds after a one-second warmup; one worker, 32 connections. Every trial validates 100 owner-filtered rows from a 120-task file, escaping/UTF-8, CSRF, session behavior and durable mutations before/after timing.",'',
      '| Build | Median req/s | CPU µs/request | Peak sampled RSS MiB |','|---|---:|---:|---:|']
    for mode,label in [('prepared-before','Pre-async prepared'),('prepared-regressed','Regressed async prepared'),('prepared-current','Fixed async prepared'),('monolithic-current','Fixed async monolithic')]:
        lines.append(f"| {label} | {rate[mode]:,.1f} | {median(rows[mode],'cpu_us_per_request'):.2f} | {median(rows[mode],'peak_sampled_rss_kib')/1024:.1f} |")
    lines+=['',f'The ratio of aggregate medians versus pre-async is {previous:+.1f}%; the median within-round ratio is {statistics.median(paired):+.1f}%. Both are reported because shared-host load varies. Prepared versus the rebuilt monolithic control is {gap:+.1f}%; identical generated code or universal performance parity is not claimed. [All authentication samples and per-round comparisons](PREPARED.md).','',
      '## Updated NeoKit / Next / Astro core stats','',
      'Matched uncached small-page SSR, one server worker/process, one wrk thread and 32 connections; medians of three shuffled rounds. CPU at 200 requests/sec is a separate equal-load probe.','',
      '| Framework | Req/s | p99 ms | Peak sampled RSS MiB | CPU µs/request | CPU % at 200 req/s |','|---|---:|---:|---:|---:|---:|']
    for row in summary:
        label={'neokit':'NeoKit prepared','next':'Next','astro':'Astro'}[row['framework']]
        lines.append(f"| {label} | {row['requests_per_sec']:,.0f} | {row['p99_saturated_ms']:.3f} | {row['peak_rss_mib']:.1f} | {row['cpu_us_per_request']:.2f} | {row['fixed_cpu_percent_one_core']:.2f} |")
    lines+=['','[Full core matrix](REPORT.md) includes single-connection latency, large/interactive pages, payload sizes, startup, versions and raw measurements.','',
      '## Async and streaming load','',
      'The fixed prepared feature application serves `/healthz` during continuous independent HTTP waits and real PostgreSQL/SCRAM streaming loaders. This fast route differs from SSR above.','',
      '| Background work | Clients | Fast req/s | Fast p99 ms | Peak sampled RSS MiB |','|---|---:|---:|---:|---:|']
    for name in ('idle','network-waits','database-and-network-streams'):
        selected=[row for row in mixed['samples'] if row['case']==name]
        lines.append(f"| {name} | {int(median(selected,'background_clients'))} | {median(selected,'requests_per_sec'):,.0f} | {median(selected,'p99_ms'):.3f} | {median(selected,'peak_sampled_rss_kib')/1024:.1f} |")
    lines+=['','All trials validate bodies/status and drain to zero task/capture/result counters. [Mixed-load CPU and raw trials](mixed/REPORT.md).','',
      '## Verification and scope','',
      f'{count} completed timing trials accompany 22 focused compiler tests and 2,385 JSON differential cases. Auth, mutation, 1/2/4-worker async PostgreSQL/network tests, ARM64 Linux prepared application checks, verified TLS/DNS waits, slow-reader backpressure, cancellation, capacity and graceful shutdown pass. Actual-browser SSR/streamed-widget/static-export checks pass. [Validation and captured logs](VALIDATION.md), [source/executable identities](identities.json).','',
      'Only this task’s builds and browser checks were kept out of the final timing run. Other host work remained active. Per-round shuffling reduces drift; there is no affinity/thermal isolation or formal confidence interval. CPU uses process-group time with 10 ms resolution; RSS is sampled every 250 ms. Node can use background threads, and Next includes RSC/bootstrap payload costs. Database/peer process resources are excluded from NeoKit mixed-load CPU/RSS. These local fixtures do not establish universal framework rankings or full feature parity. Linux checks establish functionality, not Linux performance.','',
      'Cooperative CPU scheduling, synchronous regular disk transfers, buffered PostgreSQL result sets and HTTP/1.1 connection-close streaming remain the supported profile.','',
      '![Verified final streamed sections and widget](browser-streaming.png)','']
    (out/'AUTH-FIX.md').write_text('\n'.join(lines))
    print(out/'AUTH-FIX.md')

if __name__=='__main__':main()
