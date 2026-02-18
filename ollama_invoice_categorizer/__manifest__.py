{
    'name': 'AI Invoice Categorizer powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Accounting',
    'sequence': 310,
    'summary': 'AI-powered vendor invoice analysis, account suggestions, and anomaly detection',
    'description': """
AI Invoice Categorizer powered by Ollama
=========================================
Intelligent vendor invoice analysis using AI.

Features:
---------
* **Account Suggestions** — AI analyzes invoice lines and suggests the most
  appropriate accounting account based on vendor history and line descriptions.
* **Anomaly Detection** — Flags unusual amounts, duplicate charges, or
  unexpected account assignments compared to historical patterns.
* **Expense Categorization** — Automatically classifies expenses into
  categories such as Office Supplies, Travel, IT Services, etc.
* **Confidence Scoring** — Every suggestion comes with a 0-100 % confidence
  score so you know when to review.
* **Dashboard** — Real-time statistics: analyzed invoices, anomalies found,
  average confidence, and pending reviews.
* **Batch Processing** — Analyze all pending vendor bills in one click from
  the dashboard.

Requirements:
-------------
* ``ollama_base`` module (AI engine)
* ``account`` module (Odoo Accounting)
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 149,
    'currency': 'EUR',
    'depends': ['ollama_base', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'views/invoice_dashboard_views.xml',
        'views/account_move_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
