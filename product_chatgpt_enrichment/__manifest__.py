# -*- coding: utf-8 -*-
{
    'name': 'AI Product Enrichment',
    'version': '18.0.1.0.0',
    'category': 'Sales/Products',
    'summary': 'AI-powered product enrichment with OpenAI, Gemini, Claude, Ollama and Perplexity',
    'description': """
AI Product Enrichment
=====================
Complete AI integration for automatic product data enrichment in Odoo.

Features:
---------
* Multi-Provider AI Support (OpenAI, Gemini, Anthropic Claude, Ollama, Perplexity)
* Deep Enrichment via SerpApi and ScrapingBee web search
* Automated Media and Image Import from web sources
* Dynamic Field Mapping for flexible data routing
* Bulk Enrichment from product list view
* Enrichment Queue with parallel processing
* Dashboard with real-time statistics
* Configurable AI prompts and templates
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 199,
    'currency': 'EUR',
    'depends': ['product', 'base', 'web', 'website_sale', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/chatgpt_config_data.xml',
        'data/chatgpt_prompt_data.xml',
        'data/ir_cron_data.xml',
        'views/product_enrichment_dashboard_views.xml',
        'views/chatgpt_config_views.xml',
        'views/product_template_views.xml',
        'views/product_enrichment_queue_views.xml',
        'views/batch_enrichment_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'images': ['static/description/banner.png'],
    'post_init_hook': '_post_init_fix_searxng_config',
    'installable': True,
    'application': True,
    'auto_install': False,
}
