# -*- coding: utf-8 -*-
{
    'name': "Code2ASIN Import",
    'summary': "Automate import product data via Code2ASIN",
    'description': "Automate import product data via Code2ASIN",
    'license': 'OPL-1',
    'images': ['images/main_1.png', 'images/main_screenshot.png'],
    'author': "Pixeeplay",
    'website': "https://pixeeplay.com",
    'category': 'Tools',
    'version': '6.1.37',
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
