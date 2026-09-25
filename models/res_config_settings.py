from odoo import models, fields

from .res_company import get_default_supplier_quedan_template


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # company_id es la empresa activa de Ajustes: cambiar de empresa en el
    # selector hace que se edite la plantilla de esa otra empresa.
    supplier_quedan_template = fields.Text(
        related='company_id.supplier_quedan_template',
        readonly=False,
    )

    def action_restore_supplier_quedan_template(self):
        """Restaura la plantilla predeterminada solo en la empresa activa."""
        self.ensure_one()
        self.company_id.supplier_quedan_template = get_default_supplier_quedan_template()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_preview_supplier_quedan(self):
        """PDF de vista previa con datos de ejemplo. No escribe nada."""
        self.ensure_one()
        return self.env.ref('account_supplier_quedan.action_report_supplier_quedan_preview').report_action(
            self.company_id)
