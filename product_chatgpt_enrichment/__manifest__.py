# -*- coding: utf-8 -*-
{
    'name': 'ChatGPT Product Enrichment',
    'version': '18.0.1.0.0',
    'category': 'Sales/Products',
    'summary': 'Enrich product information automatically using ChatGPT AI',
    'description': """
ChatGPT Product Enrichment
===========================
This module integrates OpenAI's ChatGPT to automatically enrich product information.

Features:
---------
* Automatic product description enhancement
* SEO-friendly content generation
* Product category and tag suggestions
* Manual enrichment button for existing products
* Configurable API settings

Requirements:
-------------
* OpenAI API key (configure in Settings > Technical > ChatGPT Configuration)
    """,
    'author': 'Pixeeplay',
    'website': 'https://github.com/pixeeplay/nus-odoo18',
    'license': 'LGPL-3',
    'depends': ['product', 'base'],
    'data': [
        'security/ir.model.access.csv',
        'data/chatgpt_config_data.xml',
        'views/chatgpt_config_views.xml',
        'views/product_template_views.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
