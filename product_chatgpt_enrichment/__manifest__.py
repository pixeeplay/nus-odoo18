# -*- coding: utf-8 -*-
{
    'name': 'Odoo AI Enrichment',
    'version': '18.0.1.0.0',
    'category': 'Sales/Products',
    'summary': 'Unified AI Enrichment (OpenAI, Gemini, Claude, Ollama, Perplexity)',
    'description': """
Odoo AI Enrichment
==================
Complete AI integration for Odoo Products.

Features:
---------
* Multi-Provider Support (OpenAI, Gemini, Anthropic, Ollama, etc.)
* Deep Enrichment via SerpApi & ScrapingBee
* Automated Media & Image Import
* Dynamic Field Mapping
* Bulk Enrichment from List View
    """,
    'author': 'Pixeeplay',
    'website': 'https://github.com/pixeeplay/nus-odoo18',
    'license': 'LGPL-3',
    'depends': ['product', 'base', 'web', 'website_sale', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/chatgpt_config_data.xml',
        'data/chatgpt_prompt_data.xml',
        'data/ir_cron_data.xml',
        'views/chatgpt_config_views.xml',
        'views/product_template_views.xml',
        'views/product_enrichment_queue_views.xml',
        'views/batch_enrichment_wizard_views.xml',
        'views/menus.xml',
    ],
    'images': ['static/description/icon.png'],
    'post_init_hook': '_post_init_fix_searxng_config',
    'installable': True,
    'application': True,
    'auto_install': False,
}
