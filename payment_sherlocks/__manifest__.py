{
    'name': 'Payment Provider: Sherlocks (Sips)',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': "Payment provider for Sherlocks/Sips (LCL, Worldline)",
    'depends': ['payment'],
    'data': [
        'views/payment_provider_views.xml',
        'views/payment_sherlocks_templates.xml',
        'data/payment_provider_data.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}
