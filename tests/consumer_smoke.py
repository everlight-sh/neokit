#!/usr/bin/env python3
"""Build a different, isolated application using the public framework contracts."""
import html
from pathlib import Path
import subprocess
import tempfile

from compiler.build import ROOT,build
from compiler.templates import literal
from tests.http_smoke import running,request


def main():
    with tempfile.TemporaryDirectory(prefix='axon-web-consumer-') as temporary:
        workspace=Path(temporary);app=workspace/'another-site';(app/'app').mkdir(parents=True)
        (app/'web.toml').write_text('[site]\nname="Another site"\n')
        (app/'app/layout.view').write_text('@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html lang="en"><head><title>{{ title }}</title></head><body><main id="web-page">{{ content }}</main>{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>')
        title='Another <site> & "café"'
        (app/'app/page.ax').write_text('import '+literal(str(ROOT/'src/lib.ax'))+'\ntype OtherProps={title:Arr u8 _,count:i64,visible:bool}\nfn other_load(request:WebRequest)->OtherProps = OtherProps {title='+literal(title)+',count=42,visible=true}\n')
        (app/'app/page.toml').write_text('loader="other_load"\nclient=false\nmode="ssr"\n')
        (app/'app/page.view').write_text('@props(title:text,count:int,visible:bool)\n<h1 title="{{ title }}">{{ title }}</h1>{% if visible %}<output>{{ count }}</output>{% endif %}')
        out,manifest=build(app,workspace/'output')
        escaped=html.escape(title).encode()
        with running(out/'server') as port:
            status,_,body=request(port)
            assert status==200 and escaped in body and b'<site>' not in body and b'<output>42</output>' in body
            assert b'<script' not in body
        assert manifest['exported_paths']==['/'] and escaped in (out/'snapshots/index.html').read_bytes()
        print('PASS isolated consumer: separate app/output, custom typed Axon loader, escaped attributes/text, bool condition, integer prop, default client entry and native SSR/static renderer')


if __name__=='__main__':main()
