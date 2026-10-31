#!/usr/bin/env python3
"""Exercise actual dev restarts for Tailwind template, imported CSS and JS edits."""
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from compiler.templates import literal
from tests.http_smoke import free_port,request


def main():
    with tempfile.TemporaryDirectory(prefix='axon-web-tailwind-dev-') as temporary:
        work=Path(temporary);app=work/'app'
        for name in ('app','styles','client'):(app/name).mkdir(parents=True)
        (app/'node_modules').symlink_to(ROOT/'node_modules',target_is_directory=True)
        (app/'web.toml').write_text('[css]\ntailwind=["site.css"]\n')
        (app/'app/layout.view').write_text('@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html><head><title>{{ title }}</title><link rel="stylesheet" href="@asset(site.css)"></head><body>{{ content }}</body></html>')
        (app/'app/page.toml').write_text('loader="load"\nmode="ssr"\nclient=false\n')
        (app/'app/page.ax').write_text('import '+literal(str(ROOT/'src/lib.ax'))+'\ntype Props={title:Arr u8 _}\nfn load(request:WebRequest)->Props=Props {title="Live Tailwind"}\n')
        view=app/'app/page.view';view.write_text('@props(title:text)\n<h1 class="p-4">{{ title }}</h1>')
        css=app/'styles/site.css';css.write_text('@import "tailwindcss" source(none);\n@import "./detail.css";\n@source "../app";\n@source "../client";\n')
        imported=app/'styles/detail.css';imported.write_text('.import-test { color: #123456; }')
        js=app/'client/index.js';js.write_text('document.documentElement.classList.add("opacity-50");')
        port=free_port();log_path=work/'dev.log'
        with log_path.open('w') as log:
            process=subprocess.Popen([str(ROOT/'web'),'dev','--app',str(app),'--out',str(work/'output'),'--port',str(port)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                def wait_for(check):
                    end=time.monotonic()+30
                    while time.monotonic()<end:
                        if process.poll() is not None:raise AssertionError(log_path.read_text())
                        try:
                            if check():return
                        except (OSError,AssertionError):pass
                        time.sleep(.1)
                    raise AssertionError(log_path.read_text())
                def page_css():
                    status,_,body=request(port);assert status==200
                    url=re.search(rb'href="(/_web/site-[^"]+\.css)"',body)[1].decode()
                    status,_,sheet=request(port,url);assert status==200
                    return body,url,sheet
                wait_for(lambda:b'.p-4{' in page_css()[2])
                before=page_css()[1]
                view.write_text('@props(title:text)\n<h1 class="grid grid-cols-3">{{ title }}</h1>')
                wait_for(lambda:b'class="grid grid-cols-3"' in page_css()[0] and b'.grid-cols-3{' in page_css()[2])
                assert page_css()[1]!=before
                imported.write_text('.import-test { color: #654321; }')
                wait_for(lambda:b'#654321' in page_css()[2])
                js.write_text('document.documentElement.classList.add("opacity-60");')
                wait_for(lambda:b'.opacity-60{' in page_css()[2] and b'.opacity-50{' not in page_css()[2])
                stable=page_css()[1]
                css.write_text('@import "./missing.css";')
                wait_for(lambda:'Tailwind failed' in log_path.read_text())
                assert page_css()[1]==stable
                css.write_text('@import "tailwindcss" source(none);\n@import "./detail.css";\n@source "../app";\n@source "../client";\n@source inline("underline");\n')
                wait_for(lambda:b'.underline{' in page_css()[2] and page_css()[1]!=stable)
                print('PASS dev watcher: template utilities, imported CSS, browser-only classes, new asset hashes, failed build keeps previous server, repair restarts successfully')
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
