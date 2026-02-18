{
    'name': 'AI Product Categorizer powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Sales/Product',
    'sequence': 302,
    'summary': 'AI-powered product categorization with Google/Amazon taxonomy support',
    'description': """
AI Product Categorizer powered by Ollama
=========================================
Automatically categorize your products using AI with support for Google Product
Taxonomy, Amazon Browse Node, and custom category trees.

Features:
---------
* AI-powered product categorization (Google / Amazon / Custom taxonomy)
* Confidence scoring (0-100%) with color-coded indicators
* Category mapping: link AI categories to Odoo product categories
* Batch categorization for entire catalogs
* Dashboard with real-time statistics
* Works with all AI providers: Ollama, OpenAI, Gemini, Anthropic, etc.

Requirements:
-------------
* Ollama AI Base module (ollama_base)
* A running AI provider (Ollama local, OpenAI, etc.)
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 99,
    'currency': 'EUR',
    'depends': [
        'ollama_base',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/categorizer_data.xml',
        'views/category_mapping_views.xml',
        'views/product_views.xml',
        'views/categorizer_dashboard_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
