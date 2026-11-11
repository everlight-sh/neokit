from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from compiler.assets import generated_assets,metadata
from compiler.build import validate_output,cache_identity,discover,realtime_options
from compiler.routes import route_path,matches,precedence,shape
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
