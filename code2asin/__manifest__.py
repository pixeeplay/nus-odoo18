# -*- coding: utf-8 -*-
{
    'name': "Code2ASIN Import",
    'summary': "Import product data from Amazon via barcode/EAN lookup with Code2ASIN API",
    'description': """
Code2ASIN Import
================
Automate product data import from Amazon using barcode and EAN code lookup.

Features:
---------
* Barcode/EAN to ASIN conversion via Code2ASIN API
* Automatic product data import (name, description, images, price)
* Dashboard with import statistics and monitoring
* Import history and detailed logs
* Bulk import support
* Configurable field mapping
    """,
    'license': 'OPL-1',
    'price': 149,
    'currency': 'EUR',
    'images': ['static/description/banner.png'],
    'author': "Antigravity",
    'website': "https://antigravity.fr",
    'support': 'support@antigravity.fr',
    'category': 'Sales/Products',
    'version': '18.0.6.1.37',
    'application': True,
    'depends': ['base', 'web', 'product'],
    'data': [
        'security/ir.model.access.csv',
        'views/dashboard_views.xml',
        'views/actions.xml',
        'views/config_views.xml',
        'views/menu.xml',
        'views/import_log_views.xml',
        'views/monitor_views.xml',
    ],
    'assets': {
    },
    'installable': True,
    'auto_install': False,
}
