import logging

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL, file_open
from odoo.tools.image import image_data_uri

from . import template_renderer

_logger = logging.getLogger(__name__)

SUPPLIER_QUEDAN_SEQUENCE_CODE = 'account_supplier_quedan.supplier_quedan'
SUPPLIER_QUEDAN_DEFAULT_TEMPLATE_PATH = 'account_supplier_quedan/data/supplier_quedan_default_template.html'


def get_default_supplier_quedan_template():
    with file_open(SUPPLIER_QUEDAN_DEFAULT_TEMPLATE_PATH, 'rb') as template_file:
        return template_file.read().decode('utf-8')


def record_text(record, field_name):
    """Valor de texto de un campo que puede no existir (p. ej. NIT/NRC de una
    localización opcional). Nunca devuelve False/None."""
    if not record or field_name not in record._fields:
        return ''
    value = record[field_name]
    return value if isinstance(value, str) else ''


def partner_single_line_address(partner):
    """Dirección legible en una línea usando el formato estándar del país."""
    if not partner:
        return ''
    lines = (partner._display_address(without_company=True) or '').splitlines()
    return ', '.join(line.strip() for line in lines if line.strip())


class ResCompany(models.Model):
    _inherit = 'res.company'

    # El default se aplica al crear la columna (instalación) y al crear
    # empresas nuevas. Una actualización del módulo no vuelve a tocar el valor,
    # así que las plantillas personalizadas se conservan.
    supplier_quedan_template = fields.Text(
        string="Plantilla del Quedan",
        default=lambda self: get_default_supplier_quedan_template(),
        help="HTML de UN SOLO Quedan. Se imprime automáticamente dos veces "
             "(ORIGINAL y COPIA) en la misma hoja.",
    )

    supplier_quedan_sequence_id = fields.Many2one(
        comodel_name='ir.sequence',
        string="Secuencia del Quedan",
        copy=False,
        readonly=True,
        ondelete='restrict',
        groups='base.group_system',
        help="Correlativo de Quedan propio de esta empresa (Q-000001, ...). "
             "Campo técnico: el módulo lo lee siempre con sudo.",
    )

    @api.constrains('supplier_quedan_template')
    def _check_supplier_quedan_template(self):
        for company in self:
            try:
                template_renderer.validate(company.supplier_quedan_template or '')
            except template_renderer.TemplateSyntaxError as error:
                raise ValidationError(_(
                    "La plantilla del Quedan de %(company)s no es válida:\n%(error)s",
                    company=company.name, error=str(error),
                )) from error

    def _get_supplier_quedan_template(self):
        self.ensure_one()
        return self.supplier_quedan_template or get_default_supplier_quedan_template()

    # ------------------------------------------------------------------
    # SECUENCIA POR EMPRESA
    # ------------------------------------------------------------------
    # Camino normal: la secuencia se crea al instalar el módulo (post_init_hook)
    # para las empresas existentes y en create() para las nuevas, así que al
    # imprimir nunca hay una "primera creación" concurrente.

    def _supplier_quedan_sequence_vals(self):
        self.ensure_one()
        return {
            'name': _("Quedan de Proveedores - %s", self.name),
            'code': SUPPLIER_QUEDAN_SEQUENCE_CODE,
            'prefix': 'Q-',
            'padding': 6,
            'number_next': 1,
            'number_increment': 1,
            'implementation': 'no_gap',
            'use_date_range': False,
            'company_id': self.id,
        }

    def _supplier_quedan_find_existing_sequence(self):
        """Secuencia del Quedan ya existente para esta empresa exacta (p. ej.
        creada por una versión anterior). Si hubiera varias, se adopta la más
        avanzada para no repetir números ya emitidos."""
        self.ensure_one()
        sequences = self.env['ir.sequence'].sudo().with_context(active_test=False).search([
            ('code', '=', SUPPLIER_QUEDAN_SEQUENCE_CODE),
            ('company_id', '=', self.id),
        ])
        return sequences.sorted(lambda seq: (-seq.number_next, seq.id))[:1]

    def _supplier_quedan_ensure_sequence(self):
        """Asigna una secuencia a cada empresa que no la tenga.

        Idempotente: no toca empresas que ya tienen supplier_quedan_sequence_id,
        no reemplaza secuencias y no consume correlativos (nunca llama a next).
        Se usa en la instalación y al crear empresas; sudo porque crear
        ir.sequence es una operación técnica reservada a administradores.
        """
        for company in self.sudo():
            if company.supplier_quedan_sequence_id:
                continue
            sequence = company._supplier_quedan_find_existing_sequence() or (
                self.env['ir.sequence'].sudo().create(company._supplier_quedan_sequence_vals()))
            company.supplier_quedan_sequence_id = sequence

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        companies._supplier_quedan_ensure_sequence()
        return companies

    def _get_supplier_quedan_sequence(self):
        """Secuencia del Quedan de esta empresa (sudo: ir.sequence es técnico).

        El llamador debe haber validado antes el acceso al pago.
        """
        self.ensure_one()
        sequence = self.sudo().supplier_quedan_sequence_id
        if sequence:
            return sequence
        return self._supplier_quedan_repair_sequence()

    def _supplier_quedan_repair_sequence(self):
        """Fallback de reparación, fuera del camino normal.

        Solo se usa si una empresa quedó sin secuencia (datos inconsistentes).
        La asignación se hace con un UPDATE condicional sobre la fila de la
        empresa: bajo REPEATABLE READ, si otra transacción asignó la secuencia
        en paralelo, PostgreSQL aborta esta transacción con un error de
        serialización que Odoo reintenta; en el reintento ya se ve la
        secuencia asignada. Así no se necesita ningún índice sobre ir_sequence.
        """
        self.ensure_one()
        company = self.sudo()
        _logger.warning(
            "La empresa %s (id %s) no tenía secuencia de Quedan; se repara.", company.name, company.id)
        sequence = company._supplier_quedan_find_existing_sequence()
        created = not sequence
        if created:
            sequence = self.env['ir.sequence'].sudo().create(company._supplier_quedan_sequence_vals())
        company.flush_recordset(['supplier_quedan_sequence_id'])
        self.env.cr.execute(SQL(
            "UPDATE res_company SET supplier_quedan_sequence_id = %s "
            "WHERE id = %s AND supplier_quedan_sequence_id IS NULL",
            sequence.id, company.id,
        ))
        assigned = self.env.cr.rowcount
        company.invalidate_recordset(['supplier_quedan_sequence_id'])
        if not assigned:
            # Ya tenía una asignada (caché desactualizada): usar esa.
            if created:
                sequence.unlink()
            sequence = company.supplier_quedan_sequence_id
        return sequence

    def _get_supplier_quedan_company_values(self):
        self.ensure_one()
        raw_logo = self.logo
        if isinstance(raw_logo, str):
            raw_logo = raw_logo.encode()
        logo_src = image_data_uri(raw_logo) if raw_logo else ''
        logo = Markup('<img src="%s" style="max-height: 55px; max-width: 200px;" alt="%s"/>') % (
            logo_src, escape(self.name or '')) if logo_src else ''
        return {
            'name': self.name or '',
            'nit': record_text(self, 'nit') or self.vat or '',
            'nrc': record_text(self, 'nrc'),
            'address': partner_single_line_address(self.partner_id),
            'street': self.street or '',
            'phone': self.phone or '',
            'email': self.email or '',
            'logo': logo,
            'logo_src': logo_src,
        }
