"""Local native applications for actual-browser checks. Ctrl-C stops all servers.

All credentials and data here are disposable test fixtures, never user accounts.
The control file under build/browser-check records URLs and editable dev sources.
"""
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from compiler.build import ROOT,build
from compiler.templates import literal
from tests.http_smoke import running,free_port

def fixture(app):
    for name in ('app/lab/one','app/lab/two','app/actions/test','client','public'):(app/name).mkdir(parents=True,exist_ok=True)
    (app/'web.toml').write_text('[site]\nname="Browser verification lab"\n[server]\naccess_log=true\n')
    (app/'app/layout.view').write_text('@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }}</title><link rel="stylesheet" href="/site.css"></head><body><main id="web-page" tabindex="-1">{{ content }}</main>{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>')
    (app/'app/lab/layout.view').write_text('@props(title:text,content:html,client:bool,mode:text)\n<aside data-web-persist="sidebar"><strong>Shared layout</strong><label>Sidebar draft<input id="sidebar-draft" value="original"></label><nav><a href="/lab/one" data-web-link data-web-prefetch>Page one</a> <a href="/lab/two" data-web-link data-web-prefetch>Page two</a></nav></aside><section>{{ content }}</section>')
    for name,title in [('one','Page one'),('two','Page two')]:
        page=app/'app/lab'/name
        (page/'page.toml').write_text('loader="lab_load_'+name+'"\nclient=true\nexport=true\ncache_ttl=30\n[metadata]\ndescription="'+title+' description"\n')
        (page/'page.neo').write_text('import '+literal(str(ROOT/'src/lib.neo'))+'\ntype LabProps_'+name+'={title:Arr u8 _}\nfn lab_load_'+name+'(request:WebRequest)->LabProps_'+name+'=LabProps_'+name+' {title='+literal(title)+'}\n')
        (page/'page.view').write_text('@props(title:text)\n<h1>{{ title }}</h1><p data-version>Template version one</p><label>Page draft<input id="page-draft" value=""></label><div data-web-widget="lab" data-web-key="browser-lab" data-web-props=\'{"initial":3}\'><p>Count: <output data-count>3</output></p><button data-increment>Add one</button><label>Action title<input data-title value="valid"></label><button data-action>Save action</button><p>Action: <output data-pending>idle</output></p><p data-notice role="status"></p><p data-lifecycle></p><p data-widget-version></p></div>')
    (app/'app/actions/test/route.toml').write_text('handler="lab_action"\ninput_type="LabInput"\nmethods=["POST"]\n[input]\ntitle={type="text",min=1,max=100}\ncount={type="int",min=0,max=100}\nenabled="bool"\n')
    (app/'app/actions/test/route.neo').write_text('import '+literal(str(ROOT/'src/lib.neo'))+'\nimport "std:debug"\ntype LabInput={title:Arr u8 _,count:i64,enabled:bool}\neffect fn lab_action(request:WebRequest,input:LabInput)->WebResponse=let slept=sleep_ms(400) in if str_equal(input.title,"reject") then web_status(web_json("{\\"error\\":\\"Rejected by validation\\"}"),422) else web_json("{\\"ok\\":true}")\n')
    runtime=str(ROOT/'client/runtime.js');state=str(ROOT/'client/state.js')
    (app/'client/index.js').write_text('import {start} from '+json.dumps(runtime)+';\nstart({widgets:{lab:()=>import("./widget.js")}});\n')
    (app/'client/widget.js').write_text('''export function mount(element,context){
  const count=context.state('count',context.props.initial),mounts=context.state('mounts',0),cleans=context.state('cleans',0);
  mounts.set(v=>v+1);
  const stops=[count.subscribe(v=>element.querySelector('[data-count]').textContent=v,{immediate:true})];
  element.querySelector('[data-lifecycle]').textContent=`Mounts ${mounts.get()} · Cleanups ${cleans.get()}`;
  element.querySelector('[data-widget-version]').textContent='Widget version one';
  const increment=()=>count.set(v=>v+1);
  const action=context.action('/actions/test',{csrf:'test-fixture'});
  const save=()=>action.run({title:element.querySelector('[data-title]').value,count:42,enabled:true},{optimistic:()=>{const old=count.get();count.set(v=>v+1);return()=>count.set(old);}}).then(()=>element.querySelector('[data-notice]').textContent='Saved successfully').catch(error=>element.querySelector('[data-notice]').textContent=error.message);
  stops.push(action.pending.subscribe(v=>element.querySelector('[data-pending]').textContent=v?'pending':'idle',{immediate:true}));
  element.querySelector('[data-increment]').addEventListener('click',increment);element.querySelector('[data-action]').addEventListener('click',save);
  return()=>{cleans.set(v=>v+1);for(const stop of stops)stop();element.querySelector('[data-increment]').removeEventListener('click',increment);element.querySelector('[data-action]').removeEventListener('click',save);};
}
''')
    (app/'public/site.css').write_text('body{font:17px system-ui;max-width:850px;margin:40px auto;padding:20px;color:#172033;background:#f5f7fa}aside,section{background:white;border:1px solid #d9dfe8;border-radius:12px;padding:24px;margin-bottom:18px}label{display:block;margin:12px 0}input,button{font:inherit;padding:8px;margin:6px}button,a{cursor:pointer}a{margin-right:20px}#neokit-dev-error{background:#421620;color:white;padding:24px;white-space:pre-wrap}')

def main():
    os.environ['NEOKIT_LIBRARY_CACHE']='off'
    directory=ROOT/'build/browser-check';directory.mkdir(parents=True,exist_ok=True)
    app=directory/'app';fixture(app)
    release,_=build(app,directory/'release',target='server')
    data=directory/'data';data.mkdir(exist_ok=True)
    cache=directory/'cache';cache.mkdir(exist_ok=True)
    key='browser-test-session-secret-at-least-32-bytes'
    password='browser-test-password';salt=b'browser-test-salt'
    b64=lambda value:base64.urlsafe_b64encode(value).decode().rstrip('=')
    encoded='p1.600000.'+b64(salt)+'.'+b64(hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000))
    with ExitStack() as stack:
        studio=stack.enter_context(running(ROOT/'build/studio/server'))
        features=stack.enter_context(running(ROOT/'build/features/server'))
        number=free_port();origin=f'http://127.0.0.1:{number}'
        dashboard=stack.enter_context(running(ROOT/'build/dashboard/server',2,{'NEOKIT_DATA_DIR':str(data),'NEOKIT_SESSION_SECRET':key,'NEOKIT_ORIGIN':origin,'NEOKIT_ADMIN_PASSWORD_HASH':encoded},number))
        number=free_port();lab=stack.enter_context(running(release/'server',env={'NEOKIT_ORIGIN':f'http://127.0.0.1:{number}','NEOKIT_CACHE_DIR':str(cache)},port=number))
        devport=free_port();devlog=(directory/'dev.log').open('w')
        dev=subprocess.Popen([str(ROOT/'neokit'),'dev','--app',str(app),'--out',str(directory/'dev'),'--port',str(devport)],env={**os.environ,'NEOKIT_ORIGIN':f'http://127.0.0.1:{devport}'},stdout=devlog,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            info={'studio':f'http://127.0.0.1:{studio}','features':f'http://127.0.0.1:{features}','dashboard':f'http://127.0.0.1:{dashboard}','lab':f'http://127.0.0.1:{lab}','dev':f'http://127.0.0.1:{devport}','app':str(app),'username':'admin','password':password}
            (directory/'control.json').write_text(json.dumps(info,indent=2)+'\n')
            print(json.dumps(info),flush=True)
            while dev.poll() is None:time.sleep(1)
            raise RuntimeError('Development server stopped: '+(directory/'dev.log').read_text())
        finally:
            if dev.poll() is None:
                dev.send_signal(signal.SIGINT)
                try:dev.wait(timeout=15)
                except subprocess.TimeoutExpired:os.killpg(dev.pid,signal.SIGKILL);dev.wait()
            devlog.close()

if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    try:main()
    except KeyboardInterrupt:pass
