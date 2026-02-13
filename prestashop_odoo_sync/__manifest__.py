{
    'name': 'PrestaShop Odoo Synchronization',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Synchronize orders, customers, and products from PrestaShop to Odoo',
    'description': """
        This module allows you to connect one or more PrestaShop instances to Odoo.
        It synchronizes:
        - Customers and Addresses
        - Products (mapping by SKU/Reference)
        - Sales Orders (including taxes and shipping)
    """,
    'author': 'Antigravity',
    'depends': ['base', 'sale_management', 'stock', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'views/prestashop_instance_views.xml',
        'views/prestashop_sync_log_views.xml',
        'views/prestashop_order_preview_views.xml',
        'wizard/views/prestashop_import_wizard_views.xml',
        'views/sale_order_views.xml',
        'views/menus.xml',
        'data/ir_cron_data.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
