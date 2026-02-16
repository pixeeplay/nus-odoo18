{
    'name': 'PrestaShop Product Sync',
    'version': '18.0.3.0.0',
    'category': 'Sales/Sales',
    'summary': 'Import products from PrestaShop with preview, field mapping, progressive import, images, stock and features',
    'author': 'Antigravity',
    'depends': ['prestashop_odoo_sync', 'product', 'stock', 'bus'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/prestashop_product_preview_views.xml',
        'views/prestashop_field_mapping_views.xml',
        'wizard/prestashop_product_sync_wizard_views.xml',
        'views/prestashop_instance_views.xml',
        'views/product_template_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'prestashop_product_sync/static/src/js/product_preview_list.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
