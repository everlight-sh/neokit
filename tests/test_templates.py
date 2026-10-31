from pathlib import Path
import tempfile
import unittest

from compiler.build import discover
from compiler.templates import BuildError, compile_all, compile_template, literal, parse


class Templates(unittest.TestCase):
    def compile(self, source, components=None):
        return compile_template(parse('Test',source),components or {},{'site.css':'/_web/site-123.css'})[0]

    def test_text_int_bool_html_have_distinct_emission(self):
        code=self.compile('@props(name: text, count: int, active: bool, content: html)\n<p title="{{ name }}">{{ name }} {{ count }} {{ active }} {{ content }}</p>')
        self.assertIn('html_escape(p_name)',code)
        self.assertIn('int_text(p_count)',code)
        self.assertIn('p_content.bytes',code)
        self.assertIn('if p_active then',code)

    def test_nested_conditions_and_typed_component(self):
        component=parse('Badge','@props(label: text)\n<strong>{{ label }}</strong>')
        code=self.compile('@props(name: text, active: bool)\n{% if active %}{% include Badge label=name %}{% else %}{% if !active %}off{% endif %}{% endif %}',{'Badge':component})
        self.assertIn('view_Badge(p_name).bytes',code)
        self.assertIn('if !p_active',code)

    def test_assets_are_resolved_at_build_time(self):
        self.assertIn('/_web/site-123.css',self.compile('@props()\n<link rel="stylesheet" href="@asset(site.css)">'))
        with self.assertRaises(BuildError):self.compile('@props()\n<script src="@asset(missing.js)"></script>')

    def test_unknown_and_invalid_props_rejected(self):
        for source in ('@props()\n{{ missing }}','@props(x: string)\nhello','@props(x:text,x:int)\nhello','hello', '@props(x:text)\n{% if x %}yes{% endif %}'):
            with self.subTest(source=source),self.assertRaises(BuildError):self.compile(source)

    def test_executable_contexts_rejected(self):
        for body in ('<script>{{ value }}</script>', '<style>{{ value }}</style>', '<div onclick="{{ value }}">x</div>', '<a href="{{ value }}">x</a>', '<input value={{ value }}>', '<iframe srcdoc="{{ value }}"></iframe>', '<div style="{{ value }}">x</div>', '<!-- {{ value }} -->', '<a href="javascript:alert(1)">x</a>', '<{{ value }}>x</{{ value }}>', '<input {{ value }}="x">', '<div {% if value %}hidden{% endif %}>x</div>'):
            with self.subTest(body=body),self.assertRaises(BuildError):self.compile('@props(value:text)\n'+body)

    def test_html_props_rejected_in_attributes(self):
        with self.assertRaises(BuildError):self.compile('@props(value:html)\n<div title="{{ value }}"></div>')

    def test_component_types_and_required_arguments(self):
        component=parse('Badge','@props(label:text)\n{{ label }}')
        for body in ('{% include Badge %}', '{% include Badge label=count %}', '{% include Unknown %}'):
            with self.subTest(body=body),self.assertRaises(BuildError):self.compile('@props(count:int)\n'+body,{'Badge':component})

    def test_recursive_components_rejected(self):
        a=parse('A','@props()\n{% include B %}')
        b=parse('B','@props()\n{% include A %}')
        with self.assertRaises(BuildError):compile_all([a,b],{'A':a,'B':b},{})

    def test_unbalanced_and_arbitrary_expressions_rejected(self):
        for body in ('{{ x', '{% if ok %}yes', '{% else %}', '{{ eval(x) }}', '{% for item in items %}'):
            with self.subTest(body=body),self.assertRaises(BuildError):self.compile('@props(ok:bool)\n'+body)

    def test_byte_literals_preserve_binary_unicode(self):
        self.assertEqual(literal(b'\x00\xff"\n'), '"\\x00\\xff\\"\\n"')
        self.assertIn('\\xc3\\xa9',literal('café'))

    def test_dynamic_export_paths_must_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'app/notes/[slug]';folder.mkdir(parents=True)
            (folder/'page.toml').write_text('loader="load"\npaths=["/other/path"]\n')
            (folder/'page.view').write_text('@props(title:text)\n{{ title }}')
            with self.assertRaises(BuildError):discover(root)


if __name__ == '__main__':unittest.main()
