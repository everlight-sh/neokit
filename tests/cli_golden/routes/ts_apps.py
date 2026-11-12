"""Configuration files of the applications the TypeScript tests write.

tests/core/*.test.ts, tests/extended/*.test.ts and tests/tools/*.ts build
throwaway apps with writeTree(); these are their web.toml, page.toml and
route.toml files (and the page.view props where [metadata.props] needs
them), copied so generate.py can feed them to the reference functions.
Update this snapshot when those tests change their apps.
"""

AUDIT_WEB = '''[site]
url="https://audit.test"
description="Audit site"

[[redirects]]
from="/old"
to="/pricing"

[[redirects]]
from="/docs-external"
to="https://docs.example/start"
status=301
query=false

[security]
csp="default-src 'self'; img-src 'self' https://cdn.example"
'''

# name -> {path relative to the app: file text}
TS_APPS = {
    'core-audit_features': {
        'web.toml': AUDIT_WEB,
        'app/api/own-policy/route.toml': 'handler="audit_own_policy"\n',
        'app/page.toml': 'loader="audit_home"\n[metadata.props]\ndescription="summary"\njsonld="jsonld"\n',
        'app/page.view': '@props(title:text, query:text, posts:Post[], summary:text, jsonld:text)\n@type Post(title:text, href:url, views:int)\n<h1>{{ title }}</h1>',
        'app/(marketing)/pricing/page.toml': 'loader="audit_pricing"\nmode="static"\n',
        'app/blog/[slug]/page.toml': 'loader="audit_post"\nmode="static"\npaths_fn="audit_slugs"\n',
        'app/account/page.toml': 'loader="audit_account"\nguard="audit_guard"\n',
        'app/search/page.toml': 'loader="audit_search"\ncache_ttl=60\ncache_query=["q"]\n',
        'app/private-info/page.toml': 'loader="audit_private"\ncache_control="private, max-age=30"\n',
        'app/feed.xml/route.toml': 'handler="audit_feed"\n',
        'app/api/data/route.toml': 'handler="audit_data"\nmethods=["GET","POST"]\ncors=["https://app.example"]\ncors_headers=["content-type","x-token"]\ncsrf=false\n',
    },
    'core-consumer': {
        'web.toml': '[site]\nname="Another site"\n',
        'app/page.toml': 'loader="other_load"\nclient=false\nmode="ssr"\n',
    },
    'core-scaffold': {
        'web.toml': '[site]\nname="app"\n',
        'app/page.toml': 'loader="load"\nclient=true\n',
    },
    'extended-dev_css': {
        'web.toml': '[css]\ntailwind=["site.css"]\n',
        'app/page.toml': 'loader="load"\nmode="ssr"\nclient=false\n',
    },
    'tools-browser_fixture': {
        'web.toml': '[site]\nname="Browser verification lab"\n[server]\naccess_log=true\n',
        'app/lab/one/page.toml': 'loader="lab_load_one"\nclient=true\nexport=true\ncache_ttl=30\n[metadata]\ndescription="Page one description"\n',
        'app/lab/two/page.toml': 'loader="lab_load_two"\nclient=true\nexport=true\ncache_ttl=30\n[metadata]\ndescription="Page two description"\n',
        'app/actions/test/route.toml': 'handler="lab_action"\ninput_type="LabInput"\nmethods=["POST"]\n[input]\ntitle={type="text",min=1,max=100}\ncount={type="int",min=0,max=100}\nenabled="bool"\n',
    },
}
