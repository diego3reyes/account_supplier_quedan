from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.account_supplier_quedan.models.res_company import get_default_supplier_quedan_template

REPORT = 'account_supplier_quedan.action_report_supplier_quedan'
PREVIEW_REPORT = 'account_supplier_quedan.action_report_supplier_quedan_preview'
DOCUMENT_MODEL = 'account.supplier.quedan.document'


@tagged('post_install', '-at_install', 'account_supplier_quedan')
class TestSupplierQuedanManualDocuments(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_1 = cls.company_data['company']
        cls.company_data_2 = cls.setup_other_company(name='Quedan Manual Company B')
        cls.company_2 = cls.company_data_2['company']
        cls.company_1.supplier_quedan_template = (
            '<i>{{ copy.label }}</i>{% for doc in documents %}[{{ doc.number }}]{% endfor %}')

    # ---------------------------------------------------------------- helpers

    def _payment(self, company_data=None, **vals):
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

    def _line(self, payment, **vals):
        return self.env[DOCUMENT_MODEL].create({'payment_id': payment.id, **vals})

    def _bill(self, amount=300.0, ref='FACT-AUTO'):
        bill = self.init_invoice('in_invoice', partner=self.partner_a, invoice_date='2026-01-01',
                                 amounts=[amount], post=True)
        bill.ref = ref
        return bill

    def _pay_bill(self, bill, **wizard_vals):
        return self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids,
        ).create(wizard_vals)._create_payments()

    def _render(self, records, report=REPORT, env=None):
        env = env or self.env
        html, _type = env['ir.actions.report']._render_qweb_html(report, records.ids)
        return html.decode() if isinstance(html, bytes) else str(html)

    def _user(self, login, groups, companies):
        return new_test_user(self.env, login=login, groups=groups, company_id=companies[0].id,
                             company_ids=[Command.set(companies.ids)])

    def _accounting_snapshot(self, payment, bill=None):
        moves = payment.move_id | (bill or self.env['account.move'])
        return {
            'amount': payment.amount,
            'invoice_ids': payment.invoice_ids.ids,
            'reconciled_bill_ids': payment.reconciled_bill_ids.ids,
            'move_count': self.env['account.move'].search_count([]),
            'moves': moves.read(['state', 'amount_total', 'amount_residual', 'payment_state',
                                 'invoice_date_due', 'date']),
            'lines': moves.line_ids.read(['debit', 'credit', 'amount_residual', 'reconciled',
                                          'date_maturity', 'account_id']),
        }

    # ---------------------------------------------------------------- sources

    def test_01_manual_payment_without_documents(self):
        payment = self._payment()
        info = payment._get_supplier_quedan_documents_info()
        self.assertEqual(payment._get_supplier_quedan_documents(), [])
        self.assertEqual(info['source'], 'none')
        self.assertEqual(info['total'], '')
        self.assertFalse(info['mismatch'])
        self.assertFalse(payment.supplier_quedan_documents_mismatch)

    def test_02_single_manual_line(self):
        payment = self._payment()
        self._line(payment, number='CCF-001', reference='REF-1', date='2026-01-05', amount=100.0)
        documents = payment._get_supplier_quedan_documents()
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]['number'], 'CCF-001')
        self.assertEqual(documents[0]['reference'], 'REF-1')
        self.assertEqual(documents[0]['type'], 'manual')
        self.assertTrue(documents[0]['date'])
        self.assertIn('100', documents[0]['amount'])
        self.assertEqual(documents[0]['currency'], payment.currency_id.name)
        context = payment._get_supplier_quedan_context()
        self.assertEqual(context['quedan']['documents_source'], 'manual')
        self.assertEqual(context['quedan']['documents_count'], 1)

    def test_03_manual_lines_order(self):
        payment = self._payment()
        self._line(payment, number='C', sequence=30, amount=10.0)
        self._line(payment, number='A', sequence=10, amount=10.0)
        self._line(payment, number='B', sequence=20, amount=10.0)
        self._line(payment, number='B2', sequence=20, amount=10.0)  # empate: por id
        self.assertEqual([d['number'] for d in payment._get_supplier_quedan_documents()], ['A', 'B', 'B2', 'C'])

    # ---------------------------------------------------------------- no accounting impact

    def test_04_manual_line_does_not_create_invoice_ids(self):
        payment = self._payment()
        self._line(payment, number='CCF-9', amount=100.0)
        payment.action_post()
        self.assertFalse(payment.invoice_ids)
        self.assertFalse(payment.reconciled_bill_ids)

    def test_05_06_07_manual_line_does_not_touch_accounting(self):
        bill = self._bill(amount=300.0)
        payment = self._pay_bill(bill, amount=100.0)
        payment.invalidate_recordset()
        before = self._accounting_snapshot(payment, bill)
        self.assertEqual(before['reconciled_bill_ids'], bill.ids)
        self._line(payment, number='MANUAL-1', amount=40.0)
        self._line(payment, number='MANUAL-2', amount=60.0)
        payment.supplier_quedan_document_ids[0].amount = 45.0
        payment.supplier_quedan_document_ids[-1].unlink()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(self._accounting_snapshot(payment, bill), before)

    # ---------------------------------------------------------------- totals / mismatch

    def test_08_manual_total(self):
        payment = self._payment()
        self._line(payment, number='1', amount=40.0)
        self._line(payment, number='2', amount=35.5)
        self.assertAlmostEqual(payment.supplier_quedan_manual_documents_total, 75.5)
        info = payment._get_supplier_quedan_documents_info()
        self.assertIn('75', info['total'])

    def test_09_total_equal_amount_no_mismatch(self):
        payment = self._payment(amount=100.0)
        self._line(payment, number='1', amount=60.0)
        self._line(payment, number='2', amount=40.0)
        self.assertFalse(payment.supplier_quedan_documents_mismatch)
        self.assertFalse(payment._get_supplier_quedan_context()['quedan']['documents_mismatch'])

    def test_10_total_different_mismatch_does_not_block(self):
        payment = self._payment(amount=100.0)
        self._line(payment, number='1', amount=60.0)
        self.assertTrue(payment.supplier_quedan_documents_mismatch)
        self.assertTrue(payment._get_supplier_quedan_context()['quedan']['documents_mismatch'])
        # No bloquea guardar ni imprimir.
        payment.memo = 'se guarda igual'
        payment.action_print_supplier_quedan()
        self.assertTrue(payment.supplier_quedan_number)
        self.assertIn('[1]', self._render(payment))

    def test_11_monetary_comparison_uses_currency_precision(self):
        payment = self._payment(amount=0.3)
        self._line(payment, number='1', amount=0.1)
        self._line(payment, number='2', amount=0.2)
        # 0.1 + 0.2 != 0.3 en coma flotante; con la precisión de la moneda es igual.
        self.assertNotEqual(sum(payment.supplier_quedan_document_ids.mapped('amount')), 0.3)
        self.assertFalse(payment.supplier_quedan_documents_mismatch)
        payment.supplier_quedan_document_ids[0].amount = 0.11
        self.assertTrue(payment.supplier_quedan_documents_mismatch)

    def test_partial_payment_automatic_has_no_mismatch(self):
        bill = self._bill(amount=300.0)
        payment = self._pay_bill(bill, amount=100.0)
        info = payment._get_supplier_quedan_documents_info()
        self.assertEqual(info['source'], 'automatic')
        self.assertFalse(info['mismatch'])
        self.assertFalse(payment.supplier_quedan_documents_mismatch)
        self.assertIn('300', info['documents'][0]['amount'])  # lógica automática sin cambios

    # ---------------------------------------------------------------- priority

    def test_12_manual_lines_override_automatic(self):
        bill = self._bill(ref='FACT-AUTO-12')
        payment = self._pay_bill(bill)
        self.assertEqual(payment.reconciled_bill_ids, bill)
        payment_class = type(payment)
        original = payment_class._get_supplier_quedan_automatic_moves

        # Control: sin líneas manuales el método automático SÍ se ejecuta
        # (demuestra que el patch intercepta el método correcto).
        with patch.object(payment_class, '_get_supplier_quedan_automatic_moves',
                          autospec=True, side_effect=original) as automatic:
            info = payment._get_supplier_quedan_documents_info()
        self.assertEqual(automatic.call_count, 1)
        self.assertEqual(info['source'], 'automatic')
        self.assertEqual([d['number'] for d in info['documents']], [bill.name])

        # Con una línea manual: solo la manual y el método automático no se llama.
        self._line(payment, number='MANUAL-12', reference='REF-M12', amount=10.0)
        with patch.object(payment_class, '_get_supplier_quedan_automatic_moves',
                          autospec=True, side_effect=original) as automatic:
            info = payment._get_supplier_quedan_documents_info()
            context = payment._get_supplier_quedan_context()
        automatic.assert_not_called()
        self.assertEqual(info['source'], 'manual')
        self.assertEqual(len(info['documents']), 1)
        self.assertEqual(info['documents'][0]['number'], 'MANUAL-12')
        self.assertEqual(info['documents'][0]['reference'], 'REF-M12')
        self.assertEqual(info['documents'][0]['type'], 'manual')
        self.assertEqual(context['documents'], info['documents'])
        self.assertEqual(context['quedan']['documents_source'], 'manual')
        # La relación contable con la factura sigue intacta.
        self.assertEqual(payment.reconciled_bill_ids, bill)

    def test_13_removing_manual_lines_restores_automatic(self):
        bill = self._bill()
        payment = self._pay_bill(bill)
        self._line(payment, number='MANUAL-13', amount=10.0)
        payment.supplier_quedan_document_ids.unlink()
        info = payment._get_supplier_quedan_documents_info()
        self.assertEqual(info['source'], 'automatic')
        self.assertEqual([d['number'] for d in info['documents']], [bill.name])

    def test_manual_line_requires_number(self):
        bill = self._bill()
        payment = self._pay_bill(bill)
        Document = self.env[DOCUMENT_MODEL]

        def assert_no_persisted_lines():
            payment.invalidate_recordset(['supplier_quedan_document_ids'])
            self.assertFalse(payment.supplier_quedan_document_ids)
            self.assertFalse(Document.search_count([('payment_id', '=', payment.id)]))

        # False / ausente: required=True -> NOT NULL de PostgreSQL en el INSERT.
        for values in ({}, {'number': False}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), \
                    mute_logger('odoo.sql_db'), self.cr.savepoint():
                self._line(payment, amount=10.0, **values)
                self.env.flush_all()
            assert_no_persisted_lines()

        # Vacío o solo espacios: @api.constrains('number').
        for number in ('', '   ', '\t \n'):
            with self.subTest(number=number), \
                    self.assertRaisesRegex(ValidationError, 'documento del Quedan es obligatorio'), \
                    self.cr.savepoint():
                self._line(payment, number=number, amount=10.0)
                self.env.flush_all()
            assert_no_persisted_lines()

        # Tampoco se puede vaciar una línea existente.
        line = self._line(payment, number='FACT-001', amount=10.0)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            line.number = '   '
            self.env.flush_all()
        line.invalidate_recordset(['number'])
        self.assertEqual(line.number, 'FACT-001')
        line.unlink()

        # Sin líneas manuales, el Quedan sigue mostrando la factura automática.
        info = payment._get_supplier_quedan_documents_info()
        self.assertEqual(info['source'], 'automatic')
        self.assertEqual([d['number'] for d in info['documents']], [bill.name])

    def test_manual_line_valid_numbers_kept_as_is(self):
        payment = self._payment()
        first = self._line(payment, number='FACT-001', amount=10.0, sequence=1)
        second = self._line(payment, number=' DTE-123 ', amount=10.0, sequence=2)
        self.env.flush_all()
        (first | second).invalidate_recordset(['number'])
        self.assertEqual(first.number, 'FACT-001')
        self.assertEqual(second.number, ' DTE-123 ')  # no se hace strip al guardar
        self.assertEqual([d['number'] for d in payment._get_supplier_quedan_documents()],
                         ['FACT-001', ' DTE-123 '])

    def test_manual_amount_zero_or_negative_allowed(self):
        payment = self._payment()
        self._line(payment, number='CERO', amount=0.0)
        self._line(payment, number='NC-1', amount=-25.0)
        self.assertEqual(payment.supplier_quedan_document_ids.mapped('amount'), [0.0, -25.0])

    def test_14_copy_does_not_copy_manual_lines(self):
        payment = self._payment()
        self._line(payment, number='NO-COPIAR', amount=100.0)
        duplicate = payment.copy()
        self.assertFalse(duplicate.supplier_quedan_document_ids)
        self.assertEqual(len(payment.supplier_quedan_document_ids), 1)

    # ---------------------------------------------------------------- multicompany / security

    def test_15_multicompany(self):
        payment_a = self._payment()
        payment_b = self._payment(company_data=self.company_data_2)
        line_b = self._line(payment_b, number='DOC-B', amount=100.0)
        self.assertEqual(line_b.company_id, self.company_2)
        self.assertEqual(line_b.currency_id, payment_b.currency_id)
        self.assertEqual([d['number'] for d in payment_b._get_supplier_quedan_documents()], ['DOC-B'])
        self.assertEqual(payment_a._get_supplier_quedan_documents(), [])

    def test_16_user_without_company_access(self):
        payment_b = self._payment(company_data=self.company_data_2)
        line_b = self._line(payment_b, number='DOC-B', amount=100.0)
        user = self._user('quedan_doc_company_a', 'account.group_account_invoice', self.company_1)
        Document = self.env[DOCUMENT_MODEL].with_user(user)
        self.assertFalse(Document.search([('id', '=', line_b.id)]))
        with self.assertRaises(AccessError):
            line_b.with_user(user).read(['number'])
        with self.assertRaises(AccessError):
            line_b.with_user(user).write({'number': 'X'})
        # La regla multiempresa se evalúa en _create() (check_access('create')).
        with self.assertRaises(AccessError):
            Document.create({'payment_id': payment_b.id, 'number': 'X', 'amount': 1.0})
        self.assertEqual(line_b.number, 'DOC-B')

    def test_17_readonly_user_cannot_modify(self):
        payment = self._payment()
        line = self._line(payment, number='RO', amount=100.0)
        user = self._user('quedan_doc_readonly', 'account.group_account_readonly', self.company_1)
        self.assertEqual(line.with_user(user).number, 'RO')
        with self.assertRaises(AccessError):
            line.with_user(user).write({'number': 'X'})
        with self.assertRaises(AccessError):
            self.env[DOCUMENT_MODEL].with_user(user).create({'payment_id': payment.id, 'number': 'X'})
        with self.assertRaises(AccessError):
            line.with_user(user).unlink()
        with self.assertRaises(AccessError):
            payment.with_user(user).write({'supplier_quedan_document_ids': [Command.create({'number': 'X'})]})
        self.assertEqual(payment.supplier_quedan_document_ids, line)

    def test_18_billing_user_can_manage(self):
        payment = self._payment()
        user = self._user('quedan_doc_billing', 'account.group_account_invoice', self.company_1)
        payment.with_user(user).write({'supplier_quedan_document_ids': [
            Command.create({'number': 'U-1', 'amount': 70.0}),
            Command.create({'number': 'U-2', 'amount': 30.0}),
        ]})
        lines = payment.supplier_quedan_document_ids
        self.assertEqual(lines.mapped('number'), ['U-1', 'U-2'])
        lines[0].with_user(user).write({'reference': 'R'})
        lines[1].with_user(user).unlink()
        self.assertEqual(payment.supplier_quedan_document_ids.mapped('reference'), ['R'])

    # ---------------------------------------------------------------- report

    def test_19_20_default_template_prints_manual_lines_in_both_copies(self):
        self.company_1.supplier_quedan_template = get_default_supplier_quedan_template()
        payment = self._payment()
        self._line(payment, number='MAN-001', reference='REF-A', amount=60.0, sequence=1)
        self._line(payment, number='MAN-002', reference='REF-B', amount=40.0, sequence=2)
        html = self._render(payment)
        original, copy = html.split('CORTAR', 1)
        for half in (original, copy):
            self.assertIn('MAN-001', half)
            self.assertIn('REF-B', half)
            self.assertLess(half.index('MAN-001'), half.index('MAN-002'))
        self.assertNotIn('Sin documentos relacionados.', html)
        self.assertEqual(html.count('MAN-001'), 2)

    def test_20_same_lines_in_original_and_copy(self):
        payment = self._payment()
        for index in range(3):
            self._line(payment, number='L%s' % index, amount=1.0, sequence=index)
        html = self._render(payment)
        self.assertIn('<i>ORIGINAL</i>[L0][L1][L2]', html)
        self.assertIn('<i>COPIA</i>[L0][L1][L2]', html)

    def test_21_preview_creates_nothing(self):
        counts = lambda: (  # noqa: E731
            self.env[DOCUMENT_MODEL].search_count([]),
            self.env['account.payment'].search_count([]),
            self.env['ir.sequence'].sudo().search_count([]),
            self.company_1.sudo().supplier_quedan_sequence_id.number_next_actual,
        )
        before = counts()
        html = self._render(self.company_1, report=PREVIEW_REPORT)
        self.assertIn('VISTA PREVIA', html)
        self.assertEqual(counts(), before)

    def test_escaping_manual_values(self):
        payment = self._payment()
        self._line(payment, number='<script>x</script>', amount=100.0)
        html = self._render(payment)
        self.assertIn('[&lt;script&gt;x&lt;/script&gt;]', html)
        self.assertNotIn('<script>x</script>', html)
