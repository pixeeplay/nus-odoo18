{
    'name': 'AI Email Composer powered by Ollama',
    'version': '19.0.1.0.0',
    'category': 'Productivity/Discuss',
    'sequence': 301,
    'summary': 'Draft professional emails with AI — tone selection, contextual generation',
    'description': """
AI Email Composer
=================
Draft professional emails instantly with AI assistance, directly from the Odoo mail composer.

Features:
---------
* "Compose with AI" button in the mail composer
* Tone selection: Professional, Friendly, Formal, Persuasive, Apology
* Custom email templates with tone-specific system prompts
* Contextual generation based on conversation history
* Dashboard with generation statistics
* Works with all AI providers (Ollama, OpenAI, Gemini, Anthropic)

Requirements:
-------------
* ollama_base module (free)
* Ollama server (local or remote) OR any supported cloud provider API key
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 79,
    'currency': 'EUR',
    'depends': ['ollama_base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/email_template_data.xml',
        'views/email_dashboard_views.xml',
        'views/email_template_views.xml',
        'wizard/email_compose_wizard_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
