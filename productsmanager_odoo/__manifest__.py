{
    'name': 'Products Manager',
    'summary': 'Unified product search & import from Products Manager API',
    'description': """
Products Manager for Odoo 18
=============================

Integrate your external Products Manager web app with Odoo:

* **Unified Search** — Search Odoo AND Products Manager in one view
* **Multi-supplier Pricing** — See prices & stock from all suppliers
* **One-click Import** — Import products with full supplier info
* **Bulk Import** — Select multiple products and import at once
* **Auto-sync** — Cron updates prices & stock every 6 hours
* **Field Mapping** — Configure how PM fields map to Odoo fields
* **Dashboard** — Monitor imports, syncs, and errors at a glance
    """,
    'category': 'Inventory/Products',
    'version': '18.0.1.0.0',
    'author': 'Antigravity',
    'website': 'https://antigravity.dev',
    'license': 'OPL-1',
    'depends': ['base', 'product', 'purchase'],
    'data': [
        'security/ir.model.access.csv',
        'data/pm_field_mapping_data.xml',
        'data/ir_cron_data.xml',
        'views/pm_config_views.xml',
        'views/pm_field_mapping_views.xml',
        'views/pm_sync_log_views.xml',
        'views/pm_search_views.xml',
        'views/product_views.xml',
        'views/res_config_settings_views.xml',
        'views/pm_dashboard_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'productsmanager_odoo/static/src/scss/pm_styles.scss',
        ],
    },
    'images': [
        'static/description/icon.png',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
}
