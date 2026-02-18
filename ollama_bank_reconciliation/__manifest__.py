# -*- coding: utf-8 -*-
{
    'name': 'AI Bank Reconciliation powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Accounting',
    'sequence': 311,
    'summary': 'AI-powered bank statement analysis, matching suggestions, and partner detection',
    'description': """
AI Bank Reconciliation powered by Ollama
==========================================
AI-powered bank statement line analysis for reconciliation assistance.

Features:
---------
* AI analysis of bank statement lines to identify payment purposes
* Intelligent invoice/bill matching suggestions with confidence scores
* Automatic partner detection from payment labels
* Reconciliation dashboard with real-time AI analytics
* Batch analysis of all unreconciled statement lines
* One-click application of AI suggestions
* Works with all AI providers supported by Ollama Base
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 149,
    'currency': 'EUR',
    'depends': [
        'ollama_base',
        'account',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/bank_dashboard_views.xml',
        'views/bank_statement_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
