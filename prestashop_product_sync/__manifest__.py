{
    'name': 'PrestaShop Product Sync',
    'version': '18.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'Import active products from PrestaShop with images, HTML descriptions and features',
    'author': 'Antigravity',
    'depends': ['prestashop_odoo_sync', 'product'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/prestashop_instance_views.xml',
        'views/product_template_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
