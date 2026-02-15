{
    'name': 'PrestaShop Odoo Synchronization',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Synchronize orders from PrestaShop to Odoo',
    'author': 'Antigravity',
    'depends': ['base', 'sale_management', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/prestashop_instance_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
