"""Real Tailwind compilation through the same asset path used by SSR/export."""
import tempfile
from pathlib import Path
import unittest

from compiler.build import ROOT, bundle_assets, css_inputs
from compiler.cli import fingerprint
from compiler.templates import BuildError


class CSS(unittest.TestCase):
    def test_optional_configuration_and_missing_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            (app/'web.toml').write_text('[site]\nname="CSS test"\n')
            self.assertEqual(css_inputs(app), set())
            for value in ('"site.css"', '["../secret.css"]', '["site.css", "site.css"]', '["missing.css"]'):
                (app/'web.toml').write_text('[css]\ntailwind='+value+'\n')
                with self.subTest(value=value), self.assertRaises(BuildError):
                    css_inputs(app)

    def test_templates_imported_css_and_browser_classes(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)/'app'; out = Path(temporary)/'output'
            for name in ('styles', 'app', 'components', 'client'):
                (app/name).mkdir(parents=True)
            # An independent app normally installs its own Tailwind packages.
            (app/'node_modules').symlink_to(ROOT/'node_modules', target_is_directory=True)
            (app/'web.toml').write_text('[css]\ntailwind=["site.css"]\n')
            (app/'styles/site.css').write_text('@import "tailwindcss" source(none);\n@import "./detail.css";\n@source "../app";\n@source "../components";\n@source "../client";\n')
            (app/'styles/detail.css').write_text('.custom-rule { color: rgb(1, 2, 3); }')
            view=app/'app/page.view'
            view.write_text('@props()\n<div class="grid gap-4 md:grid-cols-2">Hello</div>')
            (app/'components/Card.view').write_text('@props()\n<div class="font-bold">Card</div>')
            (app/'client/index.js').write_text('document.documentElement.classList.add("opacity-50");')
            assets, files=bundle_assets(app, out)
            css=files[assets['site.css']]
            for rule in (b'.grid{', b'.gap-4{', b'.md\\:grid-cols-2{', b'.font-bold{', b'.opacity-50{', b'.custom-rule{'):
                self.assertIn(rule, css)
            self.assertNotIn(b'.text-fuchsia-900{', css)
            self.assertNotIn(b'@import', css)
            # A source edit must update generated utilities and the immutable URL.
            before=fingerprint(app)
            view.write_text(view.read_text()+'<p class="text-fuchsia-900">New utility</p>')
            self.assertNotEqual(fingerprint(app), before)
            assets2, files2=bundle_assets(app, Path(temporary)/'output2')
            self.assertNotEqual(assets['site.css'], assets2['site.css'])
            self.assertIn(b'.text-fuchsia-900{', files2[assets2['site.css']])
            self.assertFalse(any('node_modules' in name for name in fingerprint(app)))


if __name__ == '__main__':
    unittest.main()
