{
    'name': 'Product Label Print',
    'summary': 'Professional product shelf labels and price tags for retail stores',
    'description': """
Product Label Print
===================
Generate professional product labels and price tags for retail store shelves.

Features:
---------
* Multiple label formats and sizes
* Barcode and QR code support
* Brand logo integration on labels
* Bulk label generation wizard
* Custom label templates with QWeb
* Price tag formatting with tax display
* Integration with PIM and AI Enrichment data
    """,
    'version': '19.0.1.0.0',
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 79,
    'currency': 'EUR',
    'category': 'Sales/Products',
    'depends': [
        'base',
        'product',
        'sale_management',
        'product_brand',
        'product_chatgpt_enrichment',
        'planete_pim',
    ],
    'data': [
        'security/ir.model.access.csv',
        'reports/product_label_report.xml',
        'reports/product_label_templates.xml',
        'wizard/label_print_wizard_views.xml',
        'views/product_template_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
