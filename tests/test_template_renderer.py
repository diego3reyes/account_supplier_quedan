try:
    from odoo.tests import BaseCase, tagged
    from odoo.addons.account_supplier_quedan.models import template_renderer
except ImportError:  # Ejecución aislada del renderer, sin Odoo.
    import unittest

    BaseCase = unittest.TestCase

    def tagged(*_tags):
        return lambda cls: cls

    import template_renderer  # noqa: F401 (el runner agrega models/ al sys.path)

from markupsafe import Markup

render = template_renderer.render
TemplateSyntaxError = template_renderer.TemplateSyntaxError


@tagged('post_install', '-at_install', 'account_supplier_quedan')
class TestSupplierQuedanTemplateRenderer(BaseCase):

    def test_variables_and_dotted_paths(self):
        ctx = {'company': {'name': 'ACME'}, 'quedan': {'number': 'Q-000001'}, 'copy': {'label': 'ORIGINAL'}}
        self.assertEqual(
            render('{{ company.name }}|{{quedan.number}}|{{ copy.label }}', ctx),
            'ACME|Q-000001|ORIGINAL',
        )

    def test_missing_values_are_empty(self):
        ctx = {'supplier': {'name': 'P', 'nit': False, 'nrc': None}, 'items': [1], 'obj': {'a': 1}}
        self.assertEqual(
            render('[{{ supplier.nit }}][{{ supplier.nrc }}][{{ nope.deep.path }}][{{ supplier.name.x }}]'
                   '[{{ items }}][{{ obj }}]', ctx),
            '[][][][][][]',
        )

    def test_zero_is_printed(self):
        self.assertEqual(render('{{ quedan.payment_days }}', {'quedan': {'payment_days': 0}}), '0')

    def test_values_are_escaped_template_is_not(self):
        ctx = {'supplier': {'name': '<script>alert(1)</script> & "x"'}}
        out = render('<b>{{ supplier.name }}</b>', ctx)
        self.assertIsInstance(out, Markup)
        self.assertEqual(out, '<b>&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;x&#34;</b>')

    def test_markup_values_are_not_double_escaped(self):
        ctx = {'company': {'logo': Markup('<img src="data:x"/>')}}
        self.assertEqual(render('{{ company.logo }}', ctx), '<img src="data:x"/>')

    def test_if_else_not(self):
        tpl = '{% if supplier.nit %}NIT {{ supplier.nit }}{% else %}SIN NIT{% endif %}' \
              '{% if not supplier.nrc %}-SIN NRC{% endif %}'
        self.assertEqual(render(tpl, {'supplier': {'nit': '123'}}), 'NIT 123-SIN NRC')
        self.assertEqual(render(tpl, {'supplier': {'nit': ''}}), 'SIN NIT-SIN NRC')

    def test_for_loop_and_loop_vars(self):
        tpl = '{% for doc in documents %}{{ loop.index }}:{{ doc.number }}{% if not loop.last %},{% endif %}{% endfor %}'
        ctx = {'documents': [{'number': 'F1'}, {'number': 'F2'}, {'number': 'F3'}]}
        self.assertEqual(render(tpl, ctx), '1:F1,2:F2,3:F3')
        self.assertEqual(render(tpl, {'documents': []}), '')
        self.assertEqual(render(tpl, {}), '')

    def test_nested_if_for(self):
        tpl = (
            '{% for doc in documents %}'
            '{% if doc.lines %}[{% for line in doc.lines %}{% if line.ok %}{{ line.name }}{% endif %}{% endfor %}]'
            '{% else %}(vacío){% endif %}'
            '{% endfor %}'
        )
        ctx = {'documents': [
            {'lines': [{'name': 'a', 'ok': True}, {'name': 'b', 'ok': False}, {'name': 'c', 'ok': True}]},
            {'lines': []},
        ]}
        self.assertEqual(render(tpl, ctx), '[ac](vacío)')

    def test_inner_if_closes_correctly(self):
        # Un regex no-greedy emparejaría el {% if %} externo con el {% endif %} interno.
        tpl = '{% if a %}A{% if b %}B{% endif %}C{% endif %}D'
        self.assertEqual(render(tpl, {'a': True, 'b': False}), 'ACD')
        self.assertEqual(render(tpl, {'a': False, 'b': True}), 'D')

    def test_loop_variable_shadowing(self):
        tpl = '{% for x in outer %}{% for x in x.inner %}{{ x }}{% endfor %}|{% endfor %}'
        self.assertEqual(render(tpl, {'outer': [{'inner': [1, 2]}, {'inner': [3]}]}), '12|3|')

    def test_no_code_execution(self):
        for bad in ('{{ __import__("os") }}', '{{ a + b }}', '{{ x() }}', '{% if 1 == 1 %}x{% endif %}',
                    '{% set a = 1 %}', '{% include "x" %}'):
            with self.assertRaises(TemplateSyntaxError, msg=bad):
                render(bad, {})
        # Solo dicts/listas: nunca atributos de objetos Python.
        self.assertEqual(render('{{ s.__class__ }}{{ s.upper }}', {'s': 'abc'}), '')

    def test_syntax_errors(self):
        for bad in ('{% if a %}x', '{% for a in b %}x', 'x{% endif %}', '{% if a %}x{% endfor %}',
                    '{% else %}', '{% if a %}{% else %}{% else %}{% endif %}'):
            with self.assertRaises(TemplateSyntaxError, msg=bad):
                template_renderer.validate(bad)

    def test_multiline_tags_and_text(self):
        tpl = '<p>\n{%   if  a.b   %}\n  {{\n a.b \n}}\n{% endif %}\n</p>'
        self.assertEqual(render(tpl, {'a': {'b': 'ok'}}), '<p>\n\n  ok\n\n</p>')
