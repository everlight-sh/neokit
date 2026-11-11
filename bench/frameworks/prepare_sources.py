#!/usr/bin/env python3
"""Deterministically create the matched native/SSR/SSG route adapters."""
import json
import os
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text)


def relative(path,folder):return './'+os.path.relpath(path,folder)


def main():
    native=HERE/'apps/axon'
    write(native/'web.toml','[site]\nname="Framework catalog benchmark"\n')
    library=relative(ROOT/'src/lib.neo',native)
    write(native/'model.neo',f'import "{library}"\n'+r'''
type BenchData = { title:Arr u8 _,n:i64,seed:i64,token:Arr u8 _,stamp:i64,checksum:i64,rows:WebHtml,interactive:bool }
fn bench_word(k:i64) -> Arr u8 _
  = let m=k%8 in if m==0 then "Widget" else if m==1 then "Gadget" else if m==2 then "Gizmo" else if m==3 then "Doohickey" else if m==4 then "Sprocket" else if m==5 then "Flange" else if m==6 then "Bracket" else "Grommet"
fn bench_adjective(k:i64) -> Arr u8 _
  = let m=k%8 in if m==0 then "Deluxe" else if m==1 then "Basic" else if m==2 then "Pro" else if m==3 then "Mini" else if m==4 then "Ultra" else if m==5 then "Classic" else if m==6 then "Eco" else "Max"
fn bench_number(query:Arr u8 _,key:Arr u8 _,fallback:i64,minimum:i64,maximum:i64) -> i64
  = let value=web_query_get(query,key) in let parsed=int_parse(value.value) in
    let decimal=len(value.value)>=1 && len(value.value)<=4 && loop(i=0,valid=true) while valid && i<len(value.value) next(i+1,let c=str_byte(value.value,i) in c>=48 && c<=57) yield valid in
    if value.present && decimal && parsed.ok && parsed.value>=minimum && parsed.value<=maximum then parsed.value else fallback
fn bench_row(i:i64,seed:i64) -> Arr u8 _
  = let name=concat(bench_word(i+seed)," ",bench_adjective(i*7+seed)) in
    concat("<tr data-id=\"",int_text(i),"\"><td>",int_text(i),"</td><td>",html_escape(name),"</td><td>",int_text((i*37+seed*13)%1000),".99</td><td>",int_text((i*13+seed)%50),"</td><td>",html_escape(if i%5==0 then "Ben & Jerry's \"Special\" <limited> café" else "-"),"</td></tr>")
; Ordinary HTML assembly; balanced concatenation avoids quadratic row copying.
fn bench_rows(start:i64,count:i64,seed:i64) -> Arr u8 _
  = if count<=0 then "" else if count==1 then bench_row(start,seed) else
    let left=count/2 in concat(bench_rows(start,left,seed),bench_rows(start+left,count-left,seed))
fn bench_load(request:WebRequest,default_n:i64,interactive:bool) -> BenchData
  = let n=bench_number(request.query,"n",default_n,1,1000) in let seed=bench_number(request.query,"seed",17,0,999) in
    let token=web_query_get(request.query,"token") in check n>=1 && n<=1000 in
    let checksum=sum(i in 0..n) ((i*37+seed*13)%1000+(i*13+seed)%50) in
    BenchData { title="Framework catalog",n=n,seed=seed,token=if token.present && len(token.value)<=128 then token.value else "fixture",stamp=request.stamp,checksum=checksum,rows=web_html(bench_rows(0,n,seed)),interactive=interactive }
''')
    layout='''@props(title:text,content:html,client:bool,mode:text)
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ title }}</title><link rel="stylesheet" href="@asset(site.css)"></head><body>{{ content }}{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>
'''
    view='''@props(title:text,n:int,seed:int,token:text,stamp:int,checksum:int,rows:html,interactive:bool)
<main id="benchmark" data-n="{{ n }}" data-seed="{{ seed }}"><h1>{{ title }}</h1><p class="request" id="request-token">{{ token }}</p><output class="stamp" id="render-stamp">{{ stamp }}</output>{% if interactive %}<section class="counter" data-web-widget="counter"><output data-count aria-label="Counter value" aria-live="polite">0</output><button type="button" data-increment>Add one +</button><button type="button" data-reset>Reset</button></section>{% endif %}<table><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Note</th></tr></thead><tbody id="catalog-rows">{{ rows }}</tbody><tfoot><tr><td colspan="5" id="checksum">{{ checksum }}</td></tr></tfoot></table></main>
'''
    write(native/'app/layout.view',layout)
    shutil.copyfile(HERE/'shared/site.css',native/'styles/site.css')
    runtime=relative(ROOT/'client/runtime.js',native/'client')
    counter=relative(HERE/'shared/counter.js',native/'client')
    write(native/'client/index.js',f'import {{ start }} from "{runtime}";\nstart({{widgets:{{counter:()=>import("{counter}")}}}});\n')
    for route,default,interactive in [('',3,False),('large',1000,False),('interactive',50,True)]:
        folder=native/'app'/route;loader='load_'+(route or 'home')
        write(folder/'page.toml',f'loader="{loader}"\nmode="ssr"\nclient={str(interactive).lower()}\nexport=true\n')
        write(folder/'page.neo',f'import "{relative(native/"model.neo",folder)}"\nfn {loader}(request:WebRequest) -> BenchData = bench_load(request,{default},{str(interactive).lower()})\n')
        write(folder/'page.view',view)

        astro=HERE/'apps/astro/src/pages'/((route or 'index')+'.astro')
        model=relative(HERE/'shared/model.js',astro.parent)
        contents=f'''---
import Document from "../components/Document.astro";
import {{ data }} from "{model}";
'''
        if interactive:contents+='import Counter from "../components/Counter.astro";\n'
        contents+=f'''const isStatic=process.env.BENCH_MODE==="static";
if (!isStatic) Astro.response.headers.set("Cache-Control","no-store");
const value=data(isStatic ? {{}} : Astro.url.searchParams,{default},isStatic ? 0 : Date.now());
---
<Document value={{value}}>{'<Counter/>' if interactive else ''}</Document>
'''
        write(astro,contents)

        for static in (False,True):
            project=HERE/'apps/next'/('static-app' if static else '')
            page=project/'app'/route/'page.jsx'
            imports=f'import Catalog from "{relative(HERE/"apps/next/components/Catalog.jsx",page.parent)}";\nimport {{ data }} from "{relative(HERE/"shared/model.js",page.parent)}";\n'
            if static:
                contents=imports+f'export default function Page() {{ return <Catalog value={{data({{}},{default},0)}} interactive={{{str(interactive).lower()}}}/>; }}\n'
            else:
                contents=imports+'import { connection } from "next/server";\n'+f'export default async function Page({{searchParams}}) {{ await connection(); const query=await searchParams; return <Catalog value={{data(query,{default},Date.now())}} interactive={{{str(interactive).lower()}}}/>; }}\n'
            write(page,contents)
    for static in (False,True):
        project=HERE/'apps/next'/('static-app' if static else '')
        file=project/'app/layout.jsx'
        write(file,f'import "{relative(HERE/"shared/site.css",file.parent)}";\nexport const metadata={{title:"Framework catalog"}};\nexport default function RootLayout({{children}}){{return <html lang="en"><body>{{children}}</body></html>;}}\n')
    print('Prepared native Axon, Astro and Next SSR/static source adapters (no cached results).')


if __name__=='__main__':main()
