{
    'name': 'Quedan de Proveedores',
    'version': '19.0.1.1.0',
    'category': 'Accounting',
    'summary': 'Gestión e impresión de Quedan para pagos de proveedores',
    'description': """
Quedan de Proveedores
=====================

- Campo informativo "Días para pago" en pagos a proveedor.
- Impresión del Quedan (ORIGINAL y COPIA en una sola hoja carta).
- Correlativo independiente por empresa, asignado solo en la primera impresión.
- Plantilla HTML editable por empresa desde Contabilidad > Configuración > Ajustes.
- Documentos manuales del Quedan (informativos) para pagos sin facturas en Odoo.
""",
    'author': '',
    'license': 'LGPL-3',
    'depends': [
        'account',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/account_supplier_quedan_security.xml',
        'report/account_supplier_quedan_report.xml',
        'report/account_supplier_quedan_templates.xml',
        'views/account_payment_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
