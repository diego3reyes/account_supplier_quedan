from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccountSupplierQuedanDocument(models.Model):
    """Línea manual del Quedan.

    Dato EXCLUSIVAMENTE informativo para imprimir el Quedan de un pago que no
    tiene facturas relacionadas en Odoo. No tiene ningún vínculo con
    account.move y no participa en conciliaciones, vencimientos, importes ni
    asientos.
    """
    _name = 'account.supplier.quedan.document'
    _description = "Documento manual del Quedan"
    _order = 'payment_id, sequence, id'
    _check_company_auto = True

    sequence = fields.Integer(string="Secuencia", default=10)
    payment_id = fields.Many2one(
        comodel_name='account.payment',
        string="Pago",
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    # Relacionados y almacenados: la línea siempre pertenece a la empresa y
    # usa la moneda de su pago (no puede haber monedas distintas en un pago).
    company_id = fields.Many2one(
        related='payment_id.company_id',
        store=True,
        index=True,
        readonly=True,
        precompute=True,
    )
    currency_id = fields.Many2one(
        related='payment_id.currency_id',
        store=True,
        readonly=True,
        precompute=True,
    )
    # Obligatorio: una sola línea manual reemplaza a las facturas automáticas,
    # así que una línea vacía creada por error no debe ocultarlas.
    number = fields.Char(string="Documento", required=True)
    reference = fields.Char(string="Referencia")
    date = fields.Date(string="Fecha")
    amount = fields.Monetary(string="Monto", currency_field='currency_id')

    @api.constrains('number')
    def _check_number_not_blank(self):
        # required=True impide NULL; esto impide '' o solo espacios. El valor
        # se guarda tal cual (sin strip), solo se valida que tenga contenido.
        if any(not (line.number or '').strip() for line in self):
            raise ValidationError(_("El número o nombre del documento del Quedan es obligatorio."))
