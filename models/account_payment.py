from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL, format_amount, format_date

from . import template_renderer
from .res_company import partner_single_line_address, record_text

SUPPLIER_QUEDAN_COPY_LABELS = ('ORIGINAL', 'COPIA')
SUPPLIER_QUEDAN_CANCELED_STATES = ('canceled', 'rejected')


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    # Dato exclusivamente informativo para el Quedan: no participa en ningún
    # cálculo contable, de vencimientos ni de conciliación.
    supplier_quedan_days = fields.Integer(
        string="Días para pago",
        default=0,
        help="Dato informativo que se imprime en el Quedan. No modifica la fecha del pago, "
             "vencimientos, términos de pago, facturas ni asientos contables.",
    )
    supplier_quedan_number = fields.Char(
        string="No. de Quedan",
        readonly=True,
        copy=False,
        index='btree_not_null',
        help="Correlativo por empresa asignado en la primera impresión del Quedan. "
             "Las reimpresiones conservan el mismo número.",
    )

    _supplier_quedan_days_positive = models.Constraint(
        'CHECK(supplier_quedan_days >= 0)',
        'Los días para pago del Quedan no pueden ser negativos.',
    )

    @api.constrains('supplier_quedan_days')
    def _check_supplier_quedan_days(self):
        if any(payment.supplier_quedan_days < 0 for payment in self):
            raise ValidationError(_("Los días para pago del Quedan no pueden ser negativos."))

    # -------------------------------------------------------------------------
    # CHECKS & NUMBERING
    # -------------------------------------------------------------------------

    def _is_supplier_quedan_payment(self):
        self.ensure_one()
        return self.partner_type == 'supplier' and self.payment_type == 'outbound'

    def _supplier_quedan_check(self):
        """Valida acceso de lectura y datos mínimos antes de imprimir.

        El permiso de escritura se exige después, solo para los pagos que
        todavía no tienen correlativo (ver _supplier_quedan_assign_number).
        """
        if not self:
            raise UserError(_("Seleccione al menos un pago para imprimir el Quedan."))
        # Lanza AccessError si el usuario no puede leer alguno de los pagos
        # (incluye reglas multiempresa).
        self.check_access('read')
        for payment in self:
            label = payment.name or _("Pago en borrador")
            if not payment._is_supplier_quedan_payment():
                raise UserError(_(
                    "El Quedan solo se puede imprimir para pagos enviados a proveedores (%s).", label))
            if not payment.partner_id:
                raise UserError(_(
                    "El pago %s no tiene proveedor. Seleccione un proveedor antes de imprimir el Quedan.", label))
            if not payment.company_id:
                raise UserError(_("El pago %s no tiene empresa asignada.", label))
            # Un pago cancelado/rechazado no genera un Quedan nuevo, pero el
            # que ya se había emitido puede reimprimirse (marcado ANULADO).
            if payment.state in SUPPLIER_QUEDAN_CANCELED_STATES and not payment.supplier_quedan_number:
                raise UserError(_(
                    "No se puede generar un Quedan nuevo para un pago cancelado o rechazado (%s).", label))

    def _supplier_quedan_assign_number(self):
        """Asigna el correlativo solo a los pagos que aún no tienen uno.

        - Primera impresión: modifica el pago, así que exige permiso de
          escritura (ACL + reglas de registro) y escribe SIN sudo.
        - Reimpresión (ya tiene número): no escribe nada; basta la lectura
          validada en _supplier_quedan_check.
        La fila del pago se bloquea para que dos impresiones simultáneas del
        mismo pago no consuman dos números. sudo solo se usa dentro de
        _get_supplier_quedan_sequence, para la ir.sequence técnica.
        """
        to_number = self.filtered(lambda payment: not payment.supplier_quedan_number)
        if not to_number:
            return
        try:
            to_number.check_access('write')
        except AccessError as error:
            raise AccessError(_(
                "La primera impresión del Quedan asigna el correlativo y requiere permiso para "
                "modificar el pago. Solicite a un usuario con ese permiso que lo imprima por primera vez; "
                "después podrá reimprimirlo.",
            )) from error
        for payment in to_number:
            self.env.cr.execute(SQL(
                "SELECT supplier_quedan_number FROM account_payment WHERE id = %s FOR UPDATE",
                payment.id,
            ))
            row = self.env.cr.fetchone()
            if row and row[0]:
                payment.invalidate_recordset(['supplier_quedan_number'])
                continue
            sequence = payment.company_id._get_supplier_quedan_sequence()
            payment.write({'supplier_quedan_number': sequence.next_by_id()})

    def action_print_supplier_quedan(self):
        self._supplier_quedan_check()
        self._supplier_quedan_assign_number()
        return self.env.ref('account_supplier_quedan.action_report_supplier_quedan').report_action(self)

    # -------------------------------------------------------------------------
    # RENDERING CONTEXT
    # -------------------------------------------------------------------------

    def _get_supplier_quedan_documents(self):
        """Documentos de proveedor relacionados de forma fiable con el pago.

        Se usa ``reconciled_bill_ids`` estándar de Odoo 19, que une:
        - ``invoice_ids``: facturas desde las que se registró el pago (existe
          aun en borrador, antes de generar asiento), y
        - las facturas conciliadas contra el asiento del pago.
        Solo contiene documentos de compra (in_invoice, in_refund, in_receipt).
        Un pago manual en borrador sin facturas vinculadas devuelve [].
        """
        self.ensure_one()
        moves = self.reconciled_bill_ids.filtered(
            lambda move: move.state != 'cancel' and move.company_id == self.company_id
        ).sorted(lambda move: (move.invoice_date or move.date or fields.Date.today(), move.name or ''))
        documents = []
        for move in moves:
            sign = -1 if move.move_type == 'in_refund' else 1
            doc_date = move.invoice_date or move.date
            documents.append({
                'number': move.name if move.name and move.name != '/' else '',
                'reference': move.ref or '',
                'date': format_date(self.env, doc_date) if doc_date else '',
                'amount': format_amount(self.env, sign * move.amount_total, move.currency_id),
                'currency': move.currency_id.name or '',
                'type': move.move_type or '',
            })
        return documents

    def _get_supplier_quedan_context(self):
        """Contexto común de ORIGINAL y COPIA (todo sale del propio pago)."""
        self.ensure_one()
        company = self.company_id
        partner = self.partner_id
        issue_date = self.date
        days = max(self.supplier_quedan_days or 0, 0)
        # Fecha estimada solo para impresión, en días calendario. No se
        # escribe en ningún campo del pago, factura o asiento.
        estimated_date = issue_date + timedelta(days=days) if issue_date else False
        currency = self.currency_id or company.currency_id
        documents = self._get_supplier_quedan_documents()
        return {
            'company': company._get_supplier_quedan_company_values(),
            'supplier': {
                'name': partner.name or '',
                'nit': record_text(partner, 'nit') or partner.vat or '',
                'nrc': record_text(partner, 'nrc'),
                'address': partner_single_line_address(partner),
                'phone': partner.phone or '',
                'email': partner.email or '',
            },
            'quedan': {
                'number': self.supplier_quedan_number or '',
                'date': format_date(self.env, issue_date) if issue_date else '',
                'amount': format_amount(self.env, self.amount, currency),
                'currency': currency.name or '',
                'payment_days': days,
                'payment_date': format_date(self.env, estimated_date) if estimated_date else '',
                'memo': self.memo or '',
                'reference': self.payment_reference or '',
                'payment_number': self.name if self.name and self.name != '/' and self.state != 'draft' else '',
                'documents_count': len(documents),
                'is_canceled': self.state in SUPPLIER_QUEDAN_CANCELED_STATES,
            },
            'documents': documents,
        }

    @api.model
    def _render_supplier_quedan_sheet(self, template, context):
        """Renderiza la misma plantilla dos veces; solo cambia copy.label."""
        sheet = {}
        for label in SUPPLIER_QUEDAN_COPY_LABELS:
            copy_context = dict(context, copy={'label': label, 'is_original': label == 'ORIGINAL'})
            try:
                sheet[label] = template_renderer.render(template, copy_context)
            except template_renderer.TemplateSyntaxError as error:
                raise UserError(_(
                    "La plantilla del Quedan no es válida. Corríjala en Contabilidad > Configuración > "
                    "Ajustes > Quedan de Proveedores.\n%s", str(error),
                )) from error
        return {'original': sheet['ORIGINAL'], 'copy': sheet['COPIA']}

    def _get_supplier_quedan_sheet(self):
        self.ensure_one()
        return self._render_supplier_quedan_sheet(
            self.company_id._get_supplier_quedan_template(),
            self._get_supplier_quedan_context(),
        )
