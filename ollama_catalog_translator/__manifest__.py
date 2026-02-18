# -*- coding: utf-8 -*-
{
    'name': 'AI Catalog Translator powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Sales/Product',
    'sequence': 307,
    'summary': 'AI-powered product catalog translation — FR, EN, DE, ES and more',
    'description': """
AI Catalog Translator powered by Ollama
========================================
Translate your entire product catalog to multiple languages with AI.

Features
--------
* Translate product names and descriptions to English, German, Spanish, Italian
* Batch translation wizard for bulk operations
* Dashboard with translation coverage statistics
* Works with all AI providers via ollama_base (Ollama, OpenAI, Gemini, Anthropic, etc.)
* Per-product translation status tracking

Requirements
------------
* ollama_base module with a configured AI provider
* product module (Sales / Inventory)
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 129,
    'currency': 'EUR',
    'depends': [
        'ollama_base',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'wizard/translation_wizard_views.xml',
        'views/translator_dashboard_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
