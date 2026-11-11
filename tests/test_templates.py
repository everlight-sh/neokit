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
        for source in ('@props()\n{{ missing }}','@props(x: string)\nhello','@props(x:text,x:int)\nhello','hello', '@props(x:int)\n{% if x %}yes{% endif %}',
                       '@props(x:text)\n{% if x > 3 %}yes{% endif %}','@props(x:int)\n{% if x == "a" %}yes{% endif %}','@props(x:int,y:text)\n{% if x == y %}yes{% endif %}',
                       '@props(x:bool)\n{% if !x == 1 %}yes{% endif %}','@props(x:int)\n{% if x + 1 > 2 %}yes{% endif %}'):
            with self.subTest(source=source),self.assertRaises(BuildError):self.compile(source)

    def test_truthiness_comparisons_and_elif(self):
        code=self.compile('@props(name:text, tags:text[], count:int, mode:text)\n'
                          '{% if name %}named{% endif %}{% if !tags %}untagged{% endif %}'
                          '{% if count >= -2 %}a{% elif mode == "dark" %}b{% elif mode != name %}c{% else %}d{% endif %}')
        self.assertIn('len(p_name)>0',code)
        self.assertIn('!(len(p_tags)>0)',code)
        self.assertIn('(p_count>=(0-2))',code)
        self.assertIn('str_equal(p_mode,"dark")',code)
        self.assertIn('!str_equal(p_mode,p_name)',code)

    def test_loop_index_and_records(self):
        template=parse('Test','@props(people:Person[], title:text)\n@type Person(name:text, age:int)\n'
                              '<h1>{{ title }}</h1>{% for person, at in people %}<p data-i="{{ at }}">{{ person.name }} {{ person.age }}</p>{% endfor %}')
        self.assertEqual(template.types['Person'],{'name':'text','age':'int'})
        code=compile_template(template,{},{})[0]
        self.assertIn('let idx_at=at in',code)
        self.assertIn('html_escape(item_person.name)',code)
        self.assertIn('int_text(item_person.age)',code)
        self.assertIn('Arr Person _',code)
        for body in ('{{ people }}','{% for person in people %}{{ person }}{% endfor %}','{% for person in people %}{{ person.missing }}{% endfor %}'):
            with self.subTest(body=body),self.assertRaises(BuildError):
                compile_template(parse('Test','@props(people:Person[])\n@type Person(name:text)\n'+body),{},{})
        for header in ('@type person(name:text)','@type Person(name:Person)','@type Person(name:text)\n@type Person(age:int)'):
            with self.subTest(header=header),self.assertRaises(BuildError):parse('Test','@props()\n'+header+'\nhi')

    def test_layout_substitution_never_rewrites_literals(self):
        from compiler.templates import substitute
        expression='concat("<p>p_title and p_content.bytes</p>",html_escape(p_title),p_content.bytes,p_titles)'
        result=substitute(expression,{'p_title':'props.title','p_content.bytes':'body()','p_content':'web_html(body())'})
        self.assertEqual(result,'concat("<p>p_title and p_content.bytes</p>",html_escape(props.title),body(),p_titles)')

    def test_url_attributes_and_meta_refresh(self):
        for body in ('<object data="{{ value }}"></object>','<video poster="{{ value }}"></video>','<svg><a xlink:href="javascript:alert(1)">x</a></svg>',
                     '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">','<meta http-equiv="refresh" content="{{ value }}">'):
            with self.subTest(body=body),self.assertRaises(BuildError):self.compile('@props(value:text)\n'+body)
        self.assertIn('p_link.value',self.compile('@props(link:url)\n<video poster="{{ link }}"></video>'))

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
