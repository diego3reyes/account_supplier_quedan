from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.account_supplier_quedan import post_init_hook
from odoo.addons.account_supplier_quedan.models.res_company import (
    SUPPLIER_QUEDAN_SEQUENCE_CODE,
    get_default_supplier_quedan_template,
)

REPORT = 'account_supplier_quedan.action_report_supplier_quedan'
PREVIEW_REPORT = 'account_supplier_quedan.action_report_supplier_quedan_preview'


@tagged('post_install', '-at_install', 'account_supplier_quedan')
class TestSupplierQuedan(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_1 = cls.company_data['company']
        cls.company_data_2 = cls.setup_other_company(name='Quedan Company B')
        cls.company_2 = cls.company_data_2['company']
        cls.company_1.supplier_quedan_template = 'A|{{ company.name }}|{{ copy.label }}|{{ quedan.number }}'
        cls.company_2.supplier_quedan_template = 'B|{{ company.name }}|{{ copy.label }}|{{ quedan.number }}'
        cls.partner_a.write({'name': 'Proveedor <b>Uno</b>'})

    def _supplier_payment(self, company_data=None, **vals):
        company_data = company_data or self.company_data
        return self.env['account.payment'].create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner_a.id,
            'amount': 100.0,
            'date': '2026-01-10',
            'journal_id': company_data['default_journal_bank'].id,
            **vals,
        })

    def _render(self, records, report=REPORT, env=None):
        env = env or self.env
        html, _type = env['ir.actions.report']._render_qweb_html(report, records.ids)
        return html.decode() if isinstance(html, bytes) else str(html)

    def _sequence(self, company):
        return self.env['ir.sequence'].sudo().with_context(active_test=False).search([
            ('code', '=', SUPPLIER_QUEDAN_SEQUENCE_CODE), ('company_id', '=', company.id)])

    def _next_number(self, company):
        return company.sudo().supplier_quedan_sequence_id.number_next_actual

    # ---------------------------------------------------------------- fields

    def test_payment_days_constraint(self):
        payment = self._supplier_payment(supplier_quedan_days=0)
        self.assertEqual(payment.supplier_quedan_days, 0)
        payment.supplier_quedan_days = 30
        self.assertEqual(payment.supplier_quedan_days, 30)
        with self.assertRaises(ValidationError):
            payment.supplier_quedan_days = -1

    def test_estimated_date_does_not_touch_accounting(self):
        bill = self.init_invoice('in_invoice', partner=self.partner_a, invoice_date='2026-01-01',
                                 amounts=[100.0], post=True)
        payment = self._supplier_payment(supplier_quedan_days=30, invoice_ids=[Command.set(bill.ids)])
        payment.action_post()
        tracked = lambda: (  # noqa: E731
            payment.date, payment.move_id.date, payment.move_id.line_ids.mapped('date_maturity'),
            bill.invoice_date, bill.invoice_date_due, bill.line_ids.mapped('date_maturity'),
            bill.invoice_payment_term_id, payment.state, payment.amount,
        )
        before = tracked()
        context = payment._get_supplier_quedan_context()
        self.assertEqual(context['quedan']['payment_days'], 30)
        self.assertEqual(
            context['quedan']['payment_date'],
            self._format_date(fields.Date.to_date('2026-02-09')),
        )
        payment.action_print_supplier_quedan()
        self._render(payment)
        payment.invalidate_recordset()
        bill.invalidate_recordset()
        self.assertEqual(before, tracked())

    def _format_date(self, value):
        from odoo.tools import format_date
        return format_date(self.env, value)

    def test_payment_days_zero(self):
        payment = self._supplier_payment(supplier_quedan_days=0)
        context = payment._get_supplier_quedan_context()
        self.assertEqual(context['quedan']['payment_days'], 0)
        self.assertEqual(context['quedan']['payment_date'], context['quedan']['date'])

    # ---------------------------------------------------------------- checks

    def test_draft_payment_can_print(self):
        payment = self._supplier_payment()
        self.assertEqual(payment.state, 'draft')
        action = payment.action_print_supplier_quedan()
        self.assertEqual(action['report_name'], 'account_supplier_quedan.report_supplier_quedan')
        self.assertTrue(payment.supplier_quedan_number.startswith('Q-'))

    def test_posted_payment_can_print(self):
        payment = self._supplier_payment()
        payment.action_post()
        self.assertNotEqual(payment.state, 'draft')
        payment.action_print_supplier_quedan()
        self.assertTrue(payment.supplier_quedan_number)

    def test_payment_without_partner(self):
        payment = self._supplier_payment(partner_id=False)
        with self.assertRaisesRegex(UserError, 'no tiene proveedor'):
            payment.action_print_supplier_quedan()
        self.assertFalse(payment.supplier_quedan_number)

    def test_customer_payment_rejected(self):
        payment = self._supplier_payment(payment_type='inbound', partner_type='customer')
        with self.assertRaises(UserError):
            payment.action_print_supplier_quedan()
        with self.assertRaises(UserError):
            self._render(payment)
        self.assertFalse(payment.supplier_quedan_number)

    def test_form_visibility_rules(self):
        arch = self.env.ref('account_supplier_quedan.view_account_payment_form_supplier_quedan').arch
        self.assertIn("partner_type != 'supplier' or payment_type != 'outbound'", arch)

    # ---------------------------------------------------------------- numbering

    def test_number_assigned_once_and_kept(self):
        payment = self._supplier_payment()
        self.assertFalse(payment.supplier_quedan_number)
        payment.action_print_supplier_quedan()
        number = payment.supplier_quedan_number
        self.assertRegex(number, r'^Q-\d{6}$')
        payment.action_print_supplier_quedan()
        self._render(payment)
        self._render(payment)
        self.assertEqual(payment.supplier_quedan_number, number)
        next_payment = self._supplier_payment()
        next_payment.action_print_supplier_quedan()
        self.assertEqual(int(next_payment.supplier_quedan_number[2:]), int(number[2:]) + 1)
        self.assertFalse(payment.copy().supplier_quedan_number)

    def test_sequences_are_per_company(self):
        pay_a1 = self._supplier_payment()
        pay_a2 = self._supplier_payment()
        pay_b1 = self._supplier_payment(company_data=self.company_data_2)
        pay_b2 = self._supplier_payment(company_data=self.company_data_2)
        self.assertEqual(pay_b1.company_id, self.company_2)
        (pay_a1 | pay_a2).action_print_supplier_quedan()
        (pay_b1 | pay_b2).action_print_supplier_quedan()
        seq_a, seq_b = self._sequence(self.company_1), self._sequence(self.company_2)
        self.assertEqual(len(seq_a), 1)
        self.assertEqual(len(seq_b), 1)
        self.assertNotEqual(seq_a, seq_b)
        self.assertEqual(pay_a1.supplier_quedan_number, pay_b1.supplier_quedan_number)
        self.assertEqual(pay_a2.supplier_quedan_number, pay_b2.supplier_quedan_number)
        self.assertNotEqual(pay_a1.supplier_quedan_number, pay_a2.supplier_quedan_number)

    # ---------------------------------------------------------------- report

    def test_original_and_copy_same_number(self):
        payment = self._supplier_payment()
        html = self._render(payment)
        number = payment.supplier_quedan_number
        self.assertTrue(number)
        self.assertEqual(html.count('A|%s|ORIGINAL|%s' % (self.company_1.name, number)), 1)
        self.assertEqual(html.count('A|%s|COPIA|%s' % (self.company_1.name, number)), 1)
        self.assertLess(html.index('|ORIGINAL|'), html.index('CORTAR'))
        self.assertLess(html.index('CORTAR'), html.index('|COPIA|'))
        # Una hoja (un article) por pago.
        self.assertEqual(html.count('class="page o_asq_page"'), 1)
        two = self._render(payment | self._supplier_payment())
        self.assertEqual(two.count('class="page o_asq_page"'), 2)

    def test_company_comes_from_payment(self):
        payment_b = self._supplier_payment(company_data=self.company_data_2)
        # Empresa activa = A; el pago es de B: se usa identidad y plantilla de B.
        env_a = self.env(context=dict(self.env.context, allowed_company_ids=[self.company_1.id, self.company_2.id]))
        self.assertEqual(env_a.company, self.company_1)
        html = self._render(payment_b, env=env_a)
        self.assertIn('B|%s|ORIGINAL|' % self.company_2.name, html)
        self.assertIn('B|%s|COPIA|' % self.company_2.name, html)
        self.assertNotIn('A|%s|' % self.company_1.name, html)
        self.assertEqual(payment_b.supplier_quedan_number, 'Q-000001')
        self.assertEqual(self._next_number(self.company_1), 1)
        self.assertEqual(self._next_number(self.company_2), 2)
        self.assertTrue(self._sequence(self.company_2))

    def test_supplier_and_escaping(self):
        self.company_1.supplier_quedan_template = '<p>{{ supplier.name }}</p><p>{{ quedan.memo }}</p>'
        payment = self._supplier_payment(memo='<img src=x onerror=alert(1)>')
        html = self._render(payment)
        self.assertIn('<p>Proveedor &lt;b&gt;Uno&lt;/b&gt;</p>', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('Proveedor <b>Uno</b>', html)

    def test_no_false_or_none_printed(self):
        self.partner_a.write({'vat': False, 'phone': False, 'email': False, 'street': False})
        self.company_1.supplier_quedan_template = (
            '[{{ supplier.nit }}][{{ supplier.nrc }}][{{ supplier.phone }}][{{ supplier.email }}]'
            '[{{ company.nrc }}][{{ quedan.memo }}][{{ does.not.exist }}]'
        )
        html = self._render(self._supplier_payment())
        self.assertIn('[][][][][][][]', html)
        for bad in ('False', 'None', 'undefined', '[object Object]'):
            self.assertNotIn('[%s]' % bad, html)

    # ---------------------------------------------------------------- documents

    def test_documents_from_register_wizard(self):
        bill = self.init_invoice('in_invoice', partner=self.partner_a, invoice_date='2026-01-01',
                                 amounts=[300.0], post=True)
        other_bill = self.init_invoice('in_invoice', partner=self.partner_a, invoice_date='2026-01-02',
                                       amounts=[999.0], post=True)
        other_bill.ref = 'OTRA-FACTURA'
        bill.ref = 'FACT-PROV-1'
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids,
        ).create({})._create_payments()
        documents = payment._get_supplier_quedan_documents()
        self.assertEqual([doc['number'] for doc in documents], [bill.name])
        self.assertEqual(documents[0]['reference'], 'FACT-PROV-1')
        self.company_1.supplier_quedan_template = (
            '{% for doc in documents %}<i>{{ doc.number }}/{{ doc.reference }}/{{ doc.amount }}</i>{% endfor %}')
        html = self._render(payment)
        self.assertEqual(html.count('FACT-PROV-1'), 2)  # ORIGINAL + COPIA
        self.assertNotIn('OTRA-FACTURA', html)
        self.assertNotIn(other_bill.name, html)

    def test_draft_payment_linked_invoice(self):
        bill = self.init_invoice('in_invoice', partner=self.partner_a, invoice_date='2026-01-01',
                                 amounts=[50.0], post=True)
        payment = self._supplier_payment(invoice_ids=[Command.set(bill.ids)])
        self.assertEqual(payment.state, 'draft')
        self.assertEqual([d['number'] for d in payment._get_supplier_quedan_documents()], [bill.name])

    def test_without_documents(self):
        self.init_invoice('in_invoice', partner=self.partner_a, invoice_date='2026-01-01',
                          amounts=[70.0], post=True)
        payment = self._supplier_payment()
        self.assertEqual(payment._get_supplier_quedan_documents(), [])
        self.company_1.supplier_quedan_template = get_default_supplier_quedan_template()
        html = self._render(payment)
        self.assertIn('Sin documentos relacionados.', html)

    # ---------------------------------------------------------------- templates

    def test_default_template_renders(self):
        self.company_1.supplier_quedan_template = False
        payment = self._supplier_payment(supplier_quedan_days=15, memo='Memo prueba')
        html = self._render(payment)
        self.assertIn('QUEDAN', html)
        self.assertIn('Firma / Sello de recibido', html)
        self.assertIn('no constituye comprobante de pago', html)
        self.assertEqual(html.count(payment.supplier_quedan_number), 2)
        self.assertNotIn('{{', html)
        self.assertNotIn('{%', html)

    def test_invalid_template_rejected(self):
        with self.assertRaises(ValidationError):
            self.company_1.supplier_quedan_template = '{% if supplier.nit %}sin cerrar'

    def test_restore_only_active_company(self):
        settings = self.env['res.config.settings'].with_company(self.company_2).create({})
        self.assertEqual(settings.company_id, self.company_2)
        self.assertTrue(settings.supplier_quedan_template.startswith('B|'))
        settings.action_restore_supplier_quedan_template()
        self.assertEqual(self.company_2.supplier_quedan_template, get_default_supplier_quedan_template())
        self.assertTrue(self.company_1.supplier_quedan_template.startswith('A|'))

    def test_settings_edit_active_company(self):
        self.env['res.config.settings'].with_company(self.company_2).create({
            'supplier_quedan_template': 'B2|{{ copy.label }}',
        }).execute()
        self.assertEqual(self.company_2.supplier_quedan_template, 'B2|{{ copy.label }}')
        self.assertTrue(self.company_1.supplier_quedan_template.startswith('A|'))

    def test_new_company_gets_default_template(self):
        company = self.env['res.company'].create({'name': 'Quedan Company C'})
        self.assertEqual(company.supplier_quedan_template, get_default_supplier_quedan_template())
        self.assertIn('quedan.is_canceled', company.supplier_quedan_template)

    def test_preview_consumes_nothing(self):
        payment_count = self.env['account.payment'].search_count([])
        sequence_count = self.env['ir.sequence'].sudo().search_count([])
        next_before = self._next_number(self.company_1)
        html = self._render(self.company_1, report=PREVIEW_REPORT)
        self.assertIn('A|%s|ORIGINAL|VISTA PREVIA' % self.company_1.name, html)
        self.assertIn('A|%s|COPIA|VISTA PREVIA' % self.company_1.name, html)
        self.assertEqual(self._next_number(self.company_1), next_before)
        self.assertEqual(self.env['ir.sequence'].sudo().search_count([]), sequence_count)
        self.assertEqual(self.env['account.payment'].search_count([]), payment_count)

    # ---------------------------------------------------------------- security

    def test_user_without_payment_access(self):
        payment = self._supplier_payment()
        user = new_test_user(self.env, login='quedan_no_access', groups='base.group_user',
                             company_id=self.company_1.id, company_ids=[Command.set(self.company_1.ids)])
        with self.assertRaises(AccessError):
            payment.with_user(user).action_print_supplier_quedan()
        self.assertFalse(payment.supplier_quedan_number)

    def test_user_of_other_company_cannot_print(self):
        payment_b = self._supplier_payment(company_data=self.company_data_2)
        user = new_test_user(self.env, login='quedan_company_a', groups='account.group_account_invoice',
                             company_id=self.company_1.id, company_ids=[Command.set(self.company_1.ids)])
        with self.assertRaises(AccessError):
            payment_b.with_user(user).action_print_supplier_quedan()
        with self.assertRaises(AccessError):
            self.env['ir.actions.report'].with_user(user)._render_qweb_html(REPORT, payment_b.ids)
        self.assertFalse(payment_b.supplier_quedan_number)

    def test_readonly_user_first_print_requires_write(self):
        payment = self._supplier_payment()
        user = new_test_user(self.env, login='quedan_readonly', groups='account.group_account_readonly',
                             company_id=self.company_1.id, company_ids=[Command.set(self.company_1.ids)])
        next_before = self._next_number(self.company_1)
        with self.assertRaisesRegex(AccessError, 'requiere permiso para modificar el pago'):
            payment.with_user(user).action_print_supplier_quedan()
        # Tampoco por la ruta del reporte (menú Imprimir / URL directa).
        with self.assertRaises(AccessError):
            self._render(payment, env=self.env(user=user))
        self.assertFalse(payment.supplier_quedan_number)
        self.assertEqual(self._next_number(self.company_1), next_before)

    def test_readonly_user_can_reprint(self):
        payment = self._supplier_payment()
        payment.action_print_supplier_quedan()
        number = payment.supplier_quedan_number
        user = new_test_user(self.env, login='quedan_readonly_reprint', groups='account.group_account_readonly',
                             company_id=self.company_1.id, company_ids=[Command.set(self.company_1.ids)])
        payment.with_user(user).action_print_supplier_quedan()
        html = self._render(payment, env=self.env(user=user))
        self.assertEqual(html.count(number), 2)
        self.assertEqual(payment.supplier_quedan_number, number)

    def test_billing_user_can_print(self):
        payment = self._supplier_payment()
        user = new_test_user(self.env, login='quedan_billing', groups='account.group_account_invoice',
                             company_id=self.company_1.id, company_ids=[Command.set(self.company_1.ids)])
        payment.with_user(user).action_print_supplier_quedan()
        self.assertTrue(payment.supplier_quedan_number)
        self.assertEqual(payment.write_uid, user)

    # ---------------------------------------------------------------- canceled

    def test_canceled_without_number_cannot_create(self):
        payment = self._supplier_payment()
        payment.action_cancel()
        self.assertEqual(payment.state, 'canceled')
        next_before = self._next_number(self.company_1)
        with self.assertRaisesRegex(UserError, 'Quedan nuevo'):
            payment.action_print_supplier_quedan()
        with self.assertRaises(UserError):
            self._render(payment)
        self.assertFalse(payment.supplier_quedan_number)
        self.assertEqual(self._next_number(self.company_1), next_before)

    def test_canceled_with_number_historical_reprint(self):
        self.company_1.supplier_quedan_template = get_default_supplier_quedan_template()
        payment = self._supplier_payment()
        payment.action_print_supplier_quedan()
        number = payment.supplier_quedan_number
        self.assertNotIn('*** ANULADO ***', self._render(payment))
        payment.action_cancel()
        self.assertEqual(payment.state, 'canceled')
        next_before = self._next_number(self.company_1)
        payment.action_print_supplier_quedan()
        html = self._render(payment)
        self.assertTrue(payment._get_supplier_quedan_context()['quedan']['is_canceled'])
        self.assertEqual(payment.supplier_quedan_number, number)
        self.assertEqual(html.count(number), 2)
        self.assertEqual(html.count('*** ANULADO ***'), 2)  # ORIGINAL + COPIA
        self.assertEqual(self._next_number(self.company_1), next_before)

    def test_rejected_state_is_canceled(self):
        payment = self._supplier_payment()
        payment.action_print_supplier_quedan()
        payment.state = 'rejected'
        self.assertTrue(payment._get_supplier_quedan_context()['quedan']['is_canceled'])
        payment.action_print_supplier_quedan()

    def test_custom_template_not_modified_for_canceled(self):
        payment = self._supplier_payment()
        payment.action_print_supplier_quedan()
        payment.action_cancel()
        html = self._render(payment)
        self.assertNotIn('ANULADO', html)
        self.assertTrue(self.company_1.supplier_quedan_template.startswith('A|'))

    def test_cancel_button_visibility(self):
        arch = self.env.ref('account_supplier_quedan.view_account_payment_form_supplier_quedan').arch
        self.assertIn("(state in ('canceled', 'rejected') and not supplier_quedan_number)", arch)

    # ---------------------------------------------------------------- sequences

    def test_every_company_has_its_sequence(self):
        for company in (self.company_1, self.company_2):
            sequence = company.sudo().supplier_quedan_sequence_id
            self.assertTrue(sequence, company.name)
            self.assertEqual(sequence.code, SUPPLIER_QUEDAN_SEQUENCE_CODE)
            self.assertEqual(sequence.company_id, company)
            self.assertEqual(sequence.prefix, 'Q-')
            self.assertEqual(sequence.padding, 6)
            self.assertEqual(sequence.implementation, 'no_gap')
            self.assertEqual(self._sequence(company), sequence)
        self.assertNotEqual(self.company_1.supplier_quedan_sequence_id, self.company_2.supplier_quedan_sequence_id)

    def test_new_company_gets_sequence_without_consuming(self):
        company = self.env['res.company'].create({'name': 'Quedan Company D'})
        sequence = company.sudo().supplier_quedan_sequence_id
        self.assertEqual(sequence.company_id, company)
        self.assertEqual(sequence.number_next_actual, 1)
        self.assertEqual(len(self._sequence(company)), 1)

    def test_install_is_idempotent(self):
        sequences = {c: c.sudo().supplier_quedan_sequence_id for c in (self.company_1, self.company_2)}
        self._supplier_payment().action_print_supplier_quedan()
        next_before = self._next_number(self.company_1)
        post_init_hook(self.env)
        post_init_hook(self.env)
        (self.company_1 | self.company_2)._supplier_quedan_ensure_sequence()
        for company, sequence in sequences.items():
            self.assertEqual(company.sudo().supplier_quedan_sequence_id, sequence)
            self.assertEqual(len(self._sequence(company)), 1)
        self.assertEqual(self._next_number(self.company_1), next_before)

    def test_repair_adopts_existing_sequence(self):
        payment = self._supplier_payment()
        payment.action_print_supplier_quedan()
        self.assertEqual(payment.supplier_quedan_number, 'Q-000001')
        sequence = self.company_1.sudo().supplier_quedan_sequence_id
        # Empresa inconsistente: perdió la referencia, pero la secuencia existe.
        self.company_1.sudo().supplier_quedan_sequence_id = False
        other = self._supplier_payment()
        other.action_print_supplier_quedan()
        self.assertEqual(self.company_1.sudo().supplier_quedan_sequence_id, sequence)
        self.assertEqual(len(self._sequence(self.company_1)), 1)
        self.assertEqual(other.supplier_quedan_number, 'Q-000002')

    def test_repair_creates_missing_sequence(self):
        company = self.env['res.company'].create({'name': 'Quedan Company E'})
        sequence = company.sudo().supplier_quedan_sequence_id
        company.sudo().supplier_quedan_sequence_id = False
        sequence.unlink()
        self.assertFalse(self._sequence(company))
        repaired = company._get_supplier_quedan_sequence()
        self.assertEqual(repaired.company_id, company)
        self.assertEqual(company.sudo().supplier_quedan_sequence_id, repaired)
        self.assertEqual(repaired.number_next_actual, 1)
        self.assertEqual(company._get_supplier_quedan_sequence(), repaired)
        self.assertEqual(len(self._sequence(company)), 1)

    def test_sequence_cannot_be_deleted_while_used(self):
        with self.assertRaises(Exception), self.cr.savepoint():
            self.company_1.sudo().supplier_quedan_sequence_id.unlink()
