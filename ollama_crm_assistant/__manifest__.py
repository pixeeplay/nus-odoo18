# -*- coding: utf-8 -*-
{
    'name': 'AI CRM Assistant powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Sales/CRM',
    'sequence': 304,
    'summary': 'AI lead scoring, email analysis, and follow-up suggestions for CRM',
    'description': """
AI CRM Assistant powered by Ollama
====================================
AI-powered CRM lead scoring and analysis tool.

Features:
---------
* AI-driven lead quality scoring (0-100)
* Email thread analysis and summarization
* Intelligent follow-up date suggestions
* CRM dashboard with AI analytics
* Automatic daily scoring via cron
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
        'crm',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/crm_data.xml',
        'views/crm_dashboard_views.xml',
        'views/crm_lead_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
