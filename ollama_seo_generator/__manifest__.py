{
    'name': 'AI SEO Generator powered by Ollama',
    'version': '19.0.1.0.0',
    'category': 'Website/Website',
    'sequence': 303,
    'summary': 'AI-powered SEO meta generation for products — titles, descriptions, keywords',
    'description': """
AI SEO Generator powered by Ollama
====================================
Generate optimized SEO metadata for your e-commerce products using AI.

Features:
---------
* AI-generated meta titles (max 60 characters)
* AI-generated meta descriptions (max 160 characters)
* Keyword extraction and suggestions
* SEO score assessment (0-100)
* Improvement suggestions
* Bulk generation across entire catalog
* One-click apply to website meta fields
* SEO audit for existing content
* Dashboard with catalog-wide SEO analytics
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
        'website_sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/seo_data.xml',
        'views/seo_dashboard_views.xml',
        'views/product_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
