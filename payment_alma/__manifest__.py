# -*- coding: utf-8 -*-

{
    'name': 'Payment Provider: Alma',
    'category': 'Accounting/Payment Providers',
    'sequence': 360,
    'summary': 'A French payment provider covering several countries in Europe.',
    'version': '19.0.1.1',
    'depends': ['payment'],
    'website': 'https://almapay.com',
    'author': 'Corbieapp',
    'support': 'jeremy.cormier.pro@gmail.com',
    'data': [
        'views/payment_provider.xml',
        'views/payment_alma_templates.xml',
        'data/payment_method_data.xml',
        'data/payment_provider_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_alma/static/src/js/payment_form.js',
            'payment_alma/static/src/js/express_checkout_form.js',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
    'price': 0.0,
    'currency': 'EUR',
}
