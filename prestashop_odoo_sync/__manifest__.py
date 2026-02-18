{
    'name': 'PrestaShop Odoo Synchronization',
    'summary': 'Synchronize orders, customers and data between PrestaShop and Odoo',
    'description': """
PrestaShop Odoo Synchronization
================================
Complete synchronization bridge between PrestaShop e-commerce and Odoo ERP.

Features:
---------
* Bi-directional order synchronization
* Customer data import and mapping
* Multi-instance PrestaShop support
* Scheduled automatic synchronization via cron
* Connection management and monitoring
* Compatible with PrestaShop 1.7+ and 8.x
    """,
    'version': '18.0.1.0.0',
    'category': 'Sales/Sales',
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 99,
    'currency': 'EUR',
    'depends': ['base', 'sale_management', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/prestashop_instance_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
