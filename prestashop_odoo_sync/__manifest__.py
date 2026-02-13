{
    'name': 'PrestaShop Odoo Synchronization',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Synchronize orders from PrestaShop to Odoo',
    'author': 'Antigravity',
    'depends': ['base', 'sale_management'],
    'data': [
        'security/ir.model.access.csv',
        'views/prestashop_instance_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
