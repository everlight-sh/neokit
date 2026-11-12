#!/usr/bin/env python3
"""Compile and exercise the routing, template, caching and metadata options
added after the 2026-11 audit in one native application and its export."""
import http.client
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from compiler.build import build
from compiler.templates import literal
from tests.http_smoke import request,running

LIB='import '+literal(str(ROOT/'src/lib.neo'))+'\n'
CSP="default-src 'self'; img-src 'self' https://cdn.example"
FILES={
 'web.toml':'''[site]
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
''',
 'app/api/own-policy/route.toml':'handler="audit_own_policy"\n',
 'app/api/own-policy/route.neo':LIB+'fn audit_own_policy(request:WebRequest) -> WebResponse = web_with_header(web_json("{}"),"Content-Security-Policy","default-src \'none\'")\n',
 'app/layout.view':'@props(title:text,content:html,client:bool,mode:text,head:html)\n<!doctype html><html><head><title>{{ title }}</title>{{ head }}</head><body><main id="web-page">{{ content }}</main></body></html>',
 'app/not-found.view':'@props()\n<!doctype html><title>Missing</title><h1>Nothing here</h1>',
 'app/page.toml':'loader="audit_home"\n[metadata.props]\ndescription="summary"\njsonld="jsonld"\n',
 'app/page.neo':LIB+r'''type Post = { title:Arr u8 _, href:WebURL, views:i64 }
type HomeProps = { title:Arr u8 _, query:Arr u8 _, posts:Arr Post _, summary:Arr u8 _, jsonld:Arr u8 _ }
fn audit_home(request:WebRequest) -> HomeProps
  = HomeProps { title="Home", query=web_query_get(request.query,"q").value,
      posts=arr(i in 0..3) Post { title=if i==0 then "First" else if i==1 then "Second" else "Third",
        href=web_url(if i==0 then "/blog/a" else "/blog/b"), views=if i==0 then 5000 else if i==1 then 0 else 7 },
      summary="Home <page> & more", jsonld="{\"@type\":\"WebSite\",\"name\":\"</script><b>x</b>\"}" }
''',
 'app/page.view':'@props(title:text, query:text, posts:Post[], summary:text, jsonld:text)\n@type Post(title:text, href:url, views:int)\n'
   '<h1>{{ title }}</h1>{% if query %}<p class="q">Results for {{ query }}</p>{% else %}<p class="q">No query</p>{% endif %}'
   '<ul>{% for post, index in posts %}<li data-rank="{{ index }}"><a href="{{ post.href }}">{{ post.title }}</a>'
   '{% if post.views >= 1000 %} popular{% elif post.views == 0 %} new{% else %} normal{% endif %}</li>{% endfor %}</ul>'
   '{% if query == "neo" %}<p>exact</p>{% endif %}',
 'app/(marketing)/pricing/page.toml':'loader="audit_pricing"\nmode="static"\n',
 'app/(marketing)/pricing/page.neo':LIB+'type PricingProps = { title:Arr u8 _ }\nfn audit_pricing(request:WebRequest) -> PricingProps = PricingProps { title="Pricing" }\n',
 'app/(marketing)/pricing/page.view':'@props(title:text)\n<h1>{{ title }}</h1>',
 'app/blog/[slug]/page.toml':'loader="audit_post"\nmode="static"\npaths_fn="audit_slugs"\n',
 'app/blog/[slug]/page.neo':LIB+'''type PostProps = { title:Arr u8 _, slug:Arr u8 _ }
fn audit_post(request:WebRequest) -> PostProps = PostProps { title="Post", slug=web_param(request,"slug").value }
fn audit_slugs() -> Arr Arr u8 _ _ = arr(i in 0..2) if i==0 then "/blog/a" else "/blog/b"
''',
 'app/blog/[slug]/page.view':'@props(title:text, slug:text)\n<h1>Post {{ slug }}</h1>',
 'app/account/page.toml':'loader="audit_account"\nguard="audit_guard"\n',
 'app/account/page.neo':LIB+'''type AccountProps = { title:Arr u8 _ }
fn audit_account(request:WebRequest) -> AccountProps = AccountProps { title="Account" }
fn audit_guard(request:WebRequest) -> WebResponse
  = let user=web_request_header(request,"x-user") in
    if str_equal(user,"ghost") then web_reply(404,"","text/plain; charset=utf-8")
    else if len(user)==0 then web_redirect("/login",303) else web_continue()
''',
 'app/account/page.view':'@props(title:text)\n<h1>{{ title }}</h1>',
 'app/search/page.toml':'loader="audit_search"\ncache_ttl=60\ncache_query=["q"]\n',
 'app/search/page.neo':LIB+'''type SearchProps = { title:Arr u8 _, q:Arr u8 _, stamp:i64 }
fn audit_search(request:WebRequest) -> SearchProps = SearchProps { title="Search", q=web_query_get(request.query,"q").value, stamp=request.stamp }
''',
 'app/search/page.view':'@props(title:text, q:text, stamp:int)\n<p>{{ q }}:{{ stamp }}</p>',
 'app/private-info/page.toml':'loader="audit_private"\ncache_control="private, max-age=30"\n',
 'app/private-info/page.neo':LIB+'type PrivateProps = { title:Arr u8 _ }\nfn audit_private(request:WebRequest) -> PrivateProps = PrivateProps { title="Private" }\n',
 'app/private-info/page.view':'@props(title:text)\n<h1>{{ title }}</h1>',
 'app/feed.xml/route.toml':'handler="audit_feed"\n',
 'app/feed.xml/route.neo':LIB+'fn audit_feed(request:WebRequest) -> WebResponse = web_reply(200,"<rss/>","application/rss+xml")\n',
 'app/api/data/route.toml':'handler="audit_data"\nmethods=["GET","POST"]\ncors=["https://app.example"]\ncors_headers=["content-type","x-token"]\ncsrf=false\n',
 'app/api/data/route.neo':LIB+'fn audit_data(request:WebRequest) -> WebResponse = web_json("{\\"ok\\":true}")\n',
}


def write_app(app):
    for name,text in FILES.items():
        path=app/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)


def main():
    with tempfile.TemporaryDirectory(prefix='neokit-audit-') as temporary:
        work=Path(temporary);app=work/'app';write_app(app)
        out,manifest=build(app,work/'server',target='server')
        assert {'source':'/old','target':'/pricing','status':308} in manifest['redirects']
        assert [r for r in manifest['routes'] if r['path']=='/blog/[slug]'][0]['paths']==['/blog/a','/blog/b']
        with tempfile.TemporaryDirectory(prefix='neokit-audit-cache-') as cache,running(out/'server',env={'NEOKIT_CACHE_DIR':cache}) as port:
            get=lambda path,headers=None,method='GET':request(port,path,method,headers)
            status,headers,home=get('/')
            assert status==200 and b'<p class="q">No query</p>' in home,home[:300]
            for rank,(title,label) in enumerate([(b'First',b' popular'),(b'Second',b' new'),(b'Third',b' normal')]):
                assert b'<li data-rank="'+str(rank).encode()+b'"><a href="/blog/'+(b'a' if rank==0 else b'b')+b'">'+title+b'</a>'+label in home,home
            assert b'<meta name="description" content="Home &lt;page&gt; &amp; more">' in home
            assert b'Audit site' not in home
            assert b'<script type="application/ld+json">{"@type":"WebSite","name":"\\u003c/script>\\u003cb>x\\u003c/b>"}</script>' in home,home
            assert b'</script><b>' not in home and home.count(b'</head>')==1
            searched=get('/?q=neo')[2];assert b'Results for neo' in searched and b'<p>exact</p>' in searched
            assert b'<p>exact</p>' not in get('/?q=other')[2]
            status,headers,pricing=get('/pricing')
            assert status==200 and b'<h1>Pricing</h1>' in pricing and headers['Cache-Control']=='public, no-cache' and headers['ETag'].startswith('W/"')
            assert get('/(marketing)/pricing')[0]==404
            for slug in ('a','b'):assert get('/blog/'+slug)[2].count(b'<h1>Post '+slug.encode()+b'</h1>')==1
            missing=get('/blog/c');assert missing[0]==404 and b'Nothing here' in missing[2]
            status,headers,_=get('/account');assert status==303 and headers['Location']=='/login'
            ghost=get('/account',{'X-User':'ghost'});assert ghost[0]==404 and b'Nothing here' in ghost[2]
            allowed=get('/account',{'X-User':'ann'});assert allowed[0]==200 and b'<h1>Account</h1>' in allowed[2]
            first=get('/search?q=a');assert first[1]['X-NeoKit-Cache']=='MISS' and first[1]['Cache-Control']=='public, max-age=60' and first[1]['Vary']=='X-Web-Fragment'
            again=get('/search?q=a&utm=1');assert again[1]['X-NeoKit-Cache']=='HIT' and again[2]==first[2]
            assert get('/search?q=b')[1]['X-NeoKit-Cache']=='MISS' and get('/search')[1]['X-NeoKit-Cache']=='MISS'
            assert get('/search?q=')[1]['X-NeoKit-Cache']=='MISS'
            status,headers,_=get('/private-info');assert headers['Cache-Control']=='private, max-age=30' and headers['Vary']=='X-Web-Fragment'
            status,headers,feed=get('/feed.xml');assert status==200 and feed==b'<rss/>' and headers['Content-Type']=='application/rss+xml'
            status,headers,_=get('/api/data',{'Origin':'https://app.example','Access-Control-Request-Method':'POST'},'OPTIONS')
            assert status==204 and headers.get('Access-Control-Allow-Origin')=='https://app.example' and set(headers.get('Access-Control-Allow-Methods','').split(', '))=={'GET','HEAD','POST'},(status,headers)
            assert headers['Access-Control-Allow-Headers']=='content-type, x-token' and headers['Vary']=='Origin' and headers['Access-Control-Max-Age']=='600',headers
            status,headers,_=get('/api/data',{'Origin':'https://evil.example'},'OPTIONS');assert status==204 and 'Access-Control-Allow-Origin' not in headers
            status,headers,body=get('/api/data',{'Origin':'https://app.example'});assert status==200 and json.loads(body)=={'ok':True} and headers['Access-Control-Allow-Origin']=='https://app.example'
            status,headers,_=get('/api/data',{'Origin':'https://evil.example'});assert status==200 and 'Access-Control-Allow-Origin' not in headers and headers['Vary']=='Origin'
            assert get('/api/data',method='DELETE')[0]==405
            status,headers,_=get('/old?x=1');assert status==308 and headers['Location']=='/pricing?x=1'
            status,headers,_=get('/docs-external?x=1');assert status==301 and headers['Location']=='https://docs.example/start'
            def policies(path,headers=None):
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
                try:
                    connection.request('GET',path,headers=headers or {});response=connection.getresponse();response.read()
                    return response.msg.get_all('Content-Security-Policy'),response.getheader('Date')
                finally:connection.close()
            for path in ('/','/account','/pricing','/blog/c','/api/data'):
                found,date=policies(path);assert found==[CSP] and date,(path,found,date)
            assert policies('/api/own-policy')[0]==["default-src 'none'"]
            sitemap=get('/sitemap.xml')[2]
            assert all(b'<loc>https://audit.test'+path+b'</loc>' in sitemap for path in (b'/',b'/pricing',b'/blog/a',b'/blog/b')) and b'/account' not in sitemap
        out,manifest=build(app,work/'export',target='static')
        snapshots=out/'snapshots'
        for page in ('index.html','pricing/index.html','blog/a/index.html','blog/b/index.html','404.html'):
            assert (snapshots/page).is_file(),page
        assert not (snapshots/'account').exists() and '/account' in manifest['omitted_server_routes']
        assert b'Nothing here' in (snapshots/'404.html').read_bytes()
        assert (snapshots/'_redirects').read_text()=='/old /pricing 308\n/docs-external https://docs.example/start 301\n'
        assert (snapshots/'_headers').read_text()=='/*\n  Content-Security-Policy: '+CSP+'\n'
        assert b'name="description"' in (snapshots/'index.html').read_bytes()
    print('PASS audit features: records, loop indexes, comparisons, (group) routes, paths_fn static routes, guards, cache_query, '
          'cache_control, dotted API routes, CORS, redirects, CSP and Date headers, dynamic metadata and JSON-LD, sitemap and export 404/_redirects/_headers')


if __name__=='__main__':main()
