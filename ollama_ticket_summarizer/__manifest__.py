{
    'name': 'AI Ticket Summarizer powered by Ollama',
    'version': '19.0.1.0.0',
    'category': 'Productivity/Discuss',
    'sequence': 306,
    'summary': 'AI-powered executive summaries of long ticket and message threads',
    'description': """
AI Ticket Summarizer
====================
Summarize long message threads (chatter) from any Odoo model that uses
mail.thread. Generates executive summaries, key discussion points,
action items, and escalation flags — all powered by AI.

Features:
---------
* Summarize any mail.thread chatter (CRM leads, helpdesk tickets, etc.)
* Executive summary in rich HTML
* Key discussion points extraction
* Action items detection
* Automatic escalation flag
* Dashboard with statistics and bulk actions
* Works with all AI providers supported by Ollama Base
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
        'views/summarizer_dashboard_views.xml',
        'views/ticket_summary_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
