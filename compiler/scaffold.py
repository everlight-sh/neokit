"""Create an isolated application with a locked dependency on this checkout."""
import json
import os
from pathlib import Path
import subprocess
import tomllib
from .build import compiler_command
from .templates import BuildError

def init(path, root, environment, standalone=False):
    path=Path(path).resolve()
    if path.exists() and any(path.iterdir()):raise BuildError('Project destination must be empty')
    path.mkdir(parents=True,exist_ok=True)
    if standalone:
        from .release import assemble
        sdk = path/'vendor/neokit-sdk'
        assemble(sdk, root, environment)
        root = sdk/'framework'
        environment = {**environment, 'NEO_HOME':str(sdk/'neo'), 'NEO_ROOT':str(sdk/'neo')}
        launcher = path/'neokit'
        launcher.write_text('''#!/bin/sh
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project"
export NEO_HOME="$project/vendor/neokit-sdk/neo"
exec "$project/vendor/neokit-sdk/framework/neokit" "$@"
''')
        launcher.chmod(0o755)
    for folder in ('app','client','public'):(path/folder).mkdir(exist_ok=True)
    relative=os.path.relpath(root,path)
    version = tomllib.loads((root/'neo.toml').read_text())['package']['version']
    (path/'neo.toml').write_text('[package]\nname='+json.dumps(path.name)+'\nversion="0.1.0"\nentry="app/page.neo"\n\n[dependencies]\nneokit={path='+json.dumps(relative)+',version='+json.dumps(version)+'}\n')
    (path/'web.toml').write_text('[site]\nname='+json.dumps(path.name)+'\n')
    (path/'app/page.neo').write_text('import "pkg:neokit"\ntype PageProps={title:Arr u8 _}\nfn load(request:WebRequest)->PageProps=PageProps {title="Hello from NeoKit"}\n')
    (path/'app/page.toml').write_text('loader="load"\nclient=true\n')
    (path/'app/page.view').write_text('@props(title:text)\n<h1>{{ title }}</h1><p>Edit app/page.view to begin.</p>\n')
    (path/'app/layout.view').write_text('@props(title:text,content:html,client:bool,mode:text)\n<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ title }}</title><link rel="stylesheet" href="/site.css"></head><body><main id="web-page" tabindex="-1">{{ content }}</main>{% if client %}<script type="module" src="@asset(client.js)"></script>{% endif %}</body></html>\n')
    runtime=os.path.relpath(root/'client/runtime.js',path/'client')
    (path/'client/index.js').write_text('import { start } from '+json.dumps(runtime)+';\nstart();\n')
    (path/'public/site.css').write_text('body{font-family:system-ui;max-width:60rem;margin:4rem auto;padding:1rem;color:#172033}h1{letter-spacing:-.03em}\n')
    (path/'.gitignore').write_text('.neokit/\n.neo-build/\nnode_modules/\n.env\n')
    subprocess.run([*compiler_command(environment),'pkg','lock',str(path)],env=environment,check=True)
    return path
