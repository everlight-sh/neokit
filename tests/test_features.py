import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from compiler.assets import generated_assets,metadata
from compiler.build import validate_output,cache_identity,discover,realtime_options,redirect_rules,redirect_response,cors_options,cors_function
from compiler.cli import change_kind
from compiler.routes import route_path,matches,precedence,shape,canonical_path
from compiler.templates import BuildError

class ExpandedFeatures(unittest.TestCase):
    def test_realtime_storage_admission_bounds(self):
        self.assertEqual(realtime_options({}),(16,128,4096))
        self.assertEqual(realtime_options({'realtime':{'channels':64,'history':1,'max_event_bytes':8192}}),(64,1,8192))
        for options in [False,{'channels':True},{'history':0},{'history':1025},{'max_event_bytes':8193},{'channels':64,'history':1024,'max_event_bytes':8192}]:
            with self.assertRaises(BuildError):realtime_options({'realtime':options})

    def test_async_page_configuration_is_typed_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            app=Path(temporary);folder=app/'app';folder.mkdir()
            (folder/'page.neo').write_text('')
            (folder/'page.view').write_text('@props(title:text)\n<h1>{{ title }}</h1>')
            config=folder/'page.toml'
            config.write_text('loader="load"\nasync=true\nasync_timeout_ms=250\n')
            route=discover(app)[0];self.assertTrue(route['config']['async'])
            for extra in ('async=1','async=true\nasync_timeout_ms=0','async=true\nasync_timeout_ms=true','async=true\nmode="static"'):
                config.write_text('loader="load"\n'+extra+'\n')
                with self.assertRaises(BuildError):discover(app)

    def test_public_cache_build_identity_tracks_locks_and_language_helpers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=root/'app';app.mkdir();toolchain=root/'neo';(toolchain/'stdlib').mkdir(parents=True)
            (app/'neo.toml').write_text('[package]\nname="app"\nversion="0.1.0"\n')
            lock=app/'neo.lock';lock.write_text('dependency revision one')
            helper=toolchain/'stdlib/render.neo';helper.write_text('fn version()->i64=1')
            with patch('compiler.build.ROOT',root/'framework'),patch('compiler.build.compiler_env',return_value={'NEO_HOME':str(toolchain)}):
                original=cache_identity(app);self.assertEqual(original,cache_identity(app))
                lock.write_text('dependency revision two');locked=cache_identity(app);self.assertNotEqual(original,locked)
                helper.write_text('fn version()->i64=2');self.assertNotEqual(locked,cache_identity(app))

    def test_route_shape_matching_and_precedence(self):
        self.assertEqual(shape('/org/[org]/items/[id]'),shape('/org/[company]/items/[item]'))
        self.assertEqual(shape('/docs/[[...slug]]'),shape('/docs/[[...path]]'))
        self.assertTrue(matches('/docs/[[...slug]]','/docs'))
        self.assertTrue(matches('/docs/[[...slug]]','/docs/a/b'))
        self.assertFalse(matches('/docs/[...slug]','/docs'))
        self.assertFalse(matches('/docs/[[...slug]]','/docs//a'))
        self.assertLess(precedence('/docs/new'),precedence('/docs/[id]'))
        self.assertLess(precedence('/docs/[id]'),precedence('/docs/[...slug]'))
        for invalid in ('[id]/[id]','[...slug]/more'):
            with self.assertRaises(BuildError):route_path(Path('/app')/invalid,Path('/app'))

    def test_migration_output_cannot_overwrite_sources_or_unmarked_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=root/'app';app.mkdir()
            with self.assertRaises(BuildError):validate_output(app,app)
            with self.assertRaises(BuildError):validate_output(app,app/'generated')
            occupied=root/'occupied';occupied.mkdir();(occupied/'valuable.txt').write_text('keep')
            with self.assertRaises(BuildError):validate_output(app,occupied)
            self.assertEqual((occupied/'valuable.txt').read_text(),'keep')

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Pillow is not installed')
    def test_metadata_images_are_hashed_resized_and_traversal_is_rejected(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            app=Path(temporary);(app/'public').mkdir()
            Image.new('RGB',(100,50),'red').save(app/'public/photo.png')
            config={'site':{'url':'https://site.test'},'images':{'files':['photo.png'],'widths':[40,1000],'formats':['webp']}}
            routes=[{'config':{'export':True},'paths':['/','/public']},{'config':{'export':False},'paths':['/private']}]
            files,bindings=generated_assets(app,config,routes)
            self.assertNotIn(b'/private',files['/sitemap.xml'])
            import io
            for width in (40,100):
                url=bindings[f'image:photo.png:{width}:webp']
                with Image.open(io.BytesIO(files[url])) as image:self.assertEqual(image.size,(width,width//2))
            self.assertEqual(generated_assets(app,config,routes), (files,bindings))
            config['images']['files']=['../../secret.png']
            with self.assertRaises(BuildError):generated_assets(app,config,routes)
        self.assertIn('content="&lt;tag&gt;"',metadata({'site':{'description':'<tag>'}},{}))

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Pillow is not installed')
    def test_image_variants_are_cached_by_content(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            app=Path(temporary);(app/'public').mkdir();cache=app/'cache'
            Image.new('RGB',(80,40),'blue').save(app/'public/photo.png')
            config={'images':{'files':['photo.png'],'widths':[20],'formats':['webp','jpeg']}}
            first=generated_assets(app,config,[],cache)
            self.assertEqual(len(list(cache.iterdir())),2)
            with patch('PIL.Image.Image.resize',side_effect=AssertionError('resized again')):
                self.assertEqual(generated_assets(app,config,[],cache),first)

    def test_public_sitemap_and_robots_take_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            app=Path(temporary);(app/'public').mkdir()
            config={'site':{'url':'https://site.test'}}
            self.assertEqual(set(generated_assets(app,config,[])[0]),{'/sitemap.xml','/robots.txt'})
            (app/'public/robots.txt').write_text('User-agent: *\n')
            self.assertEqual(set(generated_assets(app,config,[])[0]),{'/sitemap.xml'})

    def test_canonical_paths_groups_and_dotted_segments(self):
        for path in ('/','/a','/a/b-c_d','/feed.xml','/.well-known/security.txt'):
            self.assertTrue(canonical_path(path),path)
        for path in ('','a','/a/','//a','/a//b','/a/../b','/./a','/a b','/a?b',None):
            self.assertFalse(canonical_path(path),path)
        self.assertEqual(route_path(Path('/app/(marketing)/about'),Path('/app')),'/about')
        self.assertEqual(route_path(Path('/app/(shop)/(group)'),Path('/app')),'/')
        self.assertEqual(route_path(Path('/app/feed.xml'),Path('/app')),'/feed.xml')
        self.assertTrue(matches('/blog/[slug]','/blog/hello.world'))
        for invalid in ('..','a b','(bad group'):
            with self.assertRaises(BuildError):route_path(Path('/app')/invalid,Path('/app'))

    def test_redirect_rules(self):
        rules=redirect_rules({'redirects':[{'from':'/old','to':'/new'},{'from':'/docs','to':'https://docs.example/start?x=1','status':301,'query':False}]})
        self.assertEqual([(rule['status'],rule['absolute']) for rule in rules],[(308,False),(301,True)])
        self.assertIn('web_redirect(concat("/new",',redirect_response(rules[0]))
        self.assertIn('"Location","https://docs.example/start?x=1"',redirect_response(rules[1]))
        for rule in ({'from':'/a','to':'//evil.example'},{'from':'/a','to':'javascript:alert(1)'},{'from':'/a','to':'/b c'},
                     {'from':'/a','to':'/b','status':200},{'from':'/a/','to':'/b'},{'from':'/[x]','to':'/b'},{'from':'/a','to':'/b','extra':1},
                     {'from':'/a','to':'/b\\c'}):
            with self.subTest(rule=rule),self.assertRaises(BuildError):redirect_rules({'redirects':[rule]})
        with self.assertRaises(BuildError):redirect_rules({'redirects':[{'from':'/a','to':'/b'},{'from':'/a','to':'/c'}]})

    def test_cors_options(self):
        path=Path('route.toml')
        self.assertIsNone(cors_options({},path,False))
        self.assertEqual(cors_options({'cors':'*'},path,False)['origins'],['*'])
        self.assertEqual(cors_options({'cors':['https://app.example:8443']},path,False)['headers'],['content-type'])
        for config,streaming in (({'cors':['*','https://a.example']},False),({'cors':['app.example']},False),({'cors':'*'},True),
                                 ({'cors':'*','cors_credentials':True},False),({'cors':'*','input':{}},False),({'cors':['https://a.example'],'cors_headers':['bad header']},False)):
            with self.subTest(config=config),self.assertRaises(BuildError):cors_options(config,path,streaming)
        code=cors_function('Cors_x','handler(request)',['GET','HEAD','POST'],cors_options({'cors':['https://a.example']},path,False))
        self.assertIn('str_equal(origin,"https://a.example")',code)
        self.assertIn('"Access-Control-Allow-Methods","GET, HEAD, POST"',code)

    def test_page_options_guard_paths_and_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            app=Path(temporary);folder=app/'app/blog/[slug]';folder.mkdir(parents=True)
            (folder/'page.neo').write_text('')
            (folder/'page.view').write_text('@props(title:text)\n<h1>{{ title }}</h1>')
            config=folder/'page.toml'
            config.write_text('loader="load"\nguard="signed_in"\ncache_ttl=60\ncache_query=["page"]\n')
            route=discover(app)[0]
            self.assertEqual(route['guard'],'signed_in')
            self.assertFalse(route['config']['export'])
            config.write_text('loader="load"\nmode="static"\npaths_fn="all_posts"\n')
            self.assertEqual(discover(app)[0]['paths_fn'],'all_posts')
            for extra in ('guard="signed_in"\nexport=true','guard="signed_in"\nmode="static"\npaths=["/blog/a"]','cache_query=["page"]','cache_ttl=5\ncache_query=["a b"]',
                          'cache_control="public\\nx"','cache_ttl=5\ncache_control="no-cache"','guard="not valid"','mode="static"','paths=["/blog/a/"]'):
                with self.subTest(extra=extra):
                    config.write_text('loader="load"\n'+extra+'\n')
                    with self.assertRaises(BuildError):discover(app)
            static=app/'app/about';static.mkdir()
            (static/'page.neo').write_text('');(static/'page.view').write_text('@props(title:text)\nx')
            (static/'page.toml').write_text('loader="load"\npaths_fn="all"\n')
            config.write_text('loader="load"\n')
            with self.assertRaises(BuildError):discover(app)

    def test_dev_change_kinds(self):
        app=Path('/work/app')
        self.assertEqual(change_kind(app,{'/work/app/styles/site.css'}),'css')
        self.assertEqual(change_kind(app,{'/work/app/styles/site.css','/work/app/client/index.ts'}),'client')
        self.assertEqual(change_kind(app,{'/work/app/client/index.ts','/work/app/app/page.view'}),'reload')
        self.assertEqual(change_kind(app,set()),'reload')
