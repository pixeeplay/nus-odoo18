# -*- coding: utf-8 -*-
{
    'name': 'AI Helpdesk Responder powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Services/Helpdesk',
    'sequence': 305,
    'summary': 'AI-powered helpdesk with ticket classification, priority detection, and auto-responses',
    'description': """
AI Helpdesk Responder powered by Ollama
========================================
Standalone helpdesk module with AI-powered ticket management for Odoo Community Edition.

Features:
---------
* Full ticket lifecycle: create, assign, classify, resolve, close
* Kanban board with drag-and-drop stage management
* AI-powered ticket classification (category detection)
* AI priority suggestion based on ticket content
* AI sentiment analysis (positive / neutral / negative)
* AI draft response generation for quick customer replies
* One-click send AI response to customer via chatter
* Dashboard with real-time statistics and quick actions
* Full mail thread integration (chatter, followers, activities)

Requirements:
-------------
* ollama_base module (AI engine)
* Running AI provider (Ollama, OpenAI, Gemini, Anthropic, etc.)
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 149,
    'currency': 'EUR',
    'depends': ['ollama_base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/helpdesk_data.xml',
        'views/helpdesk_dashboard_views.xml',
        'views/helpdesk_ticket_views.xml',
        'views/helpdesk_stage_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
