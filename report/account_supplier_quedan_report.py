from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import format_amount, format_date


class ReportSupplierQuedan(models.AbstractModel):
    _name = 'report.account_supplier_quedan.report_supplier_quedan'
    _description = "Quedan de Proveedor"

    @api.model
    def _get_report_values(self, docids, data=None):
        payments = self.env['account.payment'].browse(docids)
        # Mismas validaciones que el botón: también cubren la impresión desde
        # el menú Imprimir o una URL /report/pdf directa.
        payments._supplier_quedan_check()
        payments._supplier_quedan_assign_number()
        return {
            'doc_ids': payments.ids,
            'doc_model': 'account.payment',
            'docs': payments,
            'quedan_sheets': [payment._get_supplier_quedan_sheet() for payment in payments],
        }


class ReportSupplierQuedanPreview(models.AbstractModel):
    """Vista previa desde Ajustes: datos de ejemplo, sin escribir nada.

    No crea pagos, asientos ni secuencias y no consume correlativos.
    """
    _name = 'report.account_supplier_quedan.report_supplier_quedan_preview'
    _description = "Vista previa del Quedan de Proveedor"

    @api.model
    def _get_report_values(self, docids, data=None):
        companies = self.env['res.company'].browse(docids)
        companies.check_access('read')
        if companies - self.env.companies:
            raise AccessError(_("Solo puede previsualizar el Quedan de sus empresas activas."))
        Payment = self.env['account.payment']
        sheets = [
            Payment._render_supplier_quedan_sheet(
                company._get_supplier_quedan_template(),
                self._get_preview_context(company),
            )
            for company in companies
        ]
        return {
            'doc_ids': companies.ids,
            'doc_model': 'res.company',
            'docs': companies,
            'quedan_sheets': sheets,
        }

    @api.model
    def _get_preview_context(self, company):
        currency = company.currency_id
        today = fields.Date.context_today(self)
        days = 30
        amounts = (1250.00, 480.50)
        documents = [
            {
                'number': 'FACT/VISTA-PREVIA/%04d' % (index + 1),
                'reference': 'REF-EJEMPLO-%s' % (index + 1),
                'date': format_date(self.env, today),
                'amount': format_amount(self.env, amount, currency),
                'currency': currency.name or '',
                'type': 'in_invoice',
            }
            for index, amount in enumerate(amounts)
        ]
        return {
            'company': company._get_supplier_quedan_company_values(),
            'supplier': {
                'name': 'PROVEEDOR DE EJEMPLO (VISTA PREVIA)',
                'nit': '0000-000000-000-0',
                'nrc': '000000-0',
                'address': 'Dirección de ejemplo, San Salvador',
                'phone': '0000-0000',
                'email': 'proveedor@ejemplo.com',
            },
            'quedan': {
                'number': 'VISTA PREVIA',
                'date': format_date(self.env, today),
                'amount': format_amount(self.env, sum(amounts), currency),
                'currency': currency.name or '',
                'payment_days': days,
                'payment_date': format_date(self.env, fields.Date.add(today, days=days)),
                'memo': 'Memo de ejemplo (vista previa)',
                'reference': '',
                'payment_number': '',
                'documents_count': len(documents),
                'is_canceled': False,
            },
            'documents': documents,
        }
