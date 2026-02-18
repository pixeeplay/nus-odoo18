# -*- coding: utf-8 -*-
{
    'name': 'MaGarantie Connector',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Integration with MaGarantie.com for warranty extensions',
    'description': """
MaGarantie Connector
=====================
Connect Odoo to the MaGarantie.com professional API to:
- Sync warranty categories and offers from MaGarantie
- Display available warranties on product forms
- Propose warranty extensions on sale orders via wizard
- Submit warranty sales to MaGarantie API
- Download warranty certificates and documents
    """,
    'author': 'Planete Technologie',
    'website': 'https://www.planetetechnologie.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'product',
        'sale_management',
        'account',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/res_config_settings_views.xml',
        'views/magarantie_category_views.xml',
        'views/magarantie_warranty_views.xml',
        'views/magarantie_sale_views.xml',
        'views/product_template_views.xml',
        'views/sale_order_views.xml',
        'views/magarantie_warranty_wizard_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
