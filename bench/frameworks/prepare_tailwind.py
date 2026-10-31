#!/usr/bin/env python3
"""Generate equivalent Tailwind fixtures in an isolated benchmark source tree."""
from pathlib import Path
import shutil
from prepare_sources import main as prepare

HERE=Path(__file__).resolve().parent


def main():
    prepare()
    shutil.copy2(HERE/'tailwind/site.css',HERE/'shared/site.css')
    axon=HERE/'apps/axon'
    with (axon/'web.toml').open('a') as f:f.write('\n[css]\ntailwind=["site.css"]\n')
    (axon/'styles/site.css').write_text('@import "../../../shared/site.css";\n')
    p=axon/'model.ax';s=p.read_text().replace('interactive:bool }','interactive:bool,metrics:bool }')
    s=s.replace('interactive=interactive }','interactive=interactive,metrics=str_equal(web_query_get(request.query,"metrics").value,"1") }');p.write_text(s)
    for p in (axon/'app').rglob('page.view'):
        s=p.read_text().replace('interactive:bool)','interactive:bool,metrics:bool)')
        s+='\n{% if metrics %}<script defer src="/browser-metrics.js"></script>{% endif %}\n';p.write_text(s)
    for app in ('axon','astro','next'):
        public=HERE/'apps'/app/'public';public.mkdir(exist_ok=True)
        shutil.copy2(HERE/'tailwind/browser-metrics.js',public/'browser-metrics.js')
    astro=HERE/'apps/astro'
    p=astro/'astro.config.mjs';s=p.read_text().replace('import node from', 'import tailwind from "@tailwindcss/vite";\nimport node from');s=s.replace('  session: false,','  session: false,\n  vite: { plugins: [tailwind()] },');p.write_text(s)
    p=astro/'src/components/Document.astro';s=p.read_text().replace('const { value }','const { value, metrics=false }').replace('</main></body>','</main>{metrics && <script is:inline defer src="/browser-metrics.js"></script>}</body>');p.write_text(s)
    for p in (astro/'src/pages').glob('*.astro'):
        s=p.read_text().replace('<Document value={value}>','<Document value={value} metrics={!isStatic && Astro.url.searchParams.get("metrics")==="1"}>');p.write_text(s)
    next_=HERE/'apps/next'
    for project in (next_,next_/'static-app'):
        (project/'postcss.config.mjs').write_text('export default {plugins:{"@tailwindcss/postcss":{}}};\n')
    p=next_/'components/Catalog.jsx';s=p.read_text().replace('interactive=false','interactive=false, metrics=false').replace('</table></main>','</table>{metrics && <script defer src="/browser-metrics.js"/>}</main>');p.write_text(s)
    for p in (next_/'app').rglob('page.jsx'):
        s=p.read_text().replace('/>;', ' metrics={query.metrics === "1"}/>;');p.write_text(s)
    # Same real utility classes and HTML content; CSS source scans all three
    # implementations explicitly, excluding compiled output and dependency trees.
    for base in (axon/'app',astro/'src/components',next_/'components',next_/'app',next_/'static-app/app'):
        for p in base.rglob('*'):
            if p.suffix not in ('.view','.astro','.jsx'):continue
            s=p.read_text()
            for a,b in {
                '<body>':'<body class="bg-[#f5f4ed] font-sans text-[15px] text-[#252b25] antialiased">' if p.suffix!='.jsx' else '<body className="bg-[#f5f4ed] font-sans text-[15px] text-[#252b25] antialiased">',
                '<main id="benchmark"':'<main class="mx-auto my-12 max-w-[1100px] px-6" id="benchmark"' if p.suffix!='.jsx' else '<main className="mx-auto my-12 max-w-[1100px] px-6" id="benchmark"',
                '<h1>':'<h1 class="mb-5 font-serif text-5xl tracking-[-2px]">' if p.suffix!='.jsx' else '<h1 className="mb-5 font-serif text-5xl tracking-[-2px]">',
                '<table>':'<table class="w-full border-collapse bg-white">' if p.suffix!='.jsx' else '<table className="w-full border-collapse bg-white">',
            }.items():s=s.replace(a,b)
            p.write_text(s)
    print('Tailwind 4 fixtures: native CLI / Astro Vite / Next PostCSS; optional visible browser metrics probe')


if __name__=='__main__':main()
