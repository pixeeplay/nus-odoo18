{
    'name': 'AI Timesheet Summary powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Human Resources/Timesheets',
    'sequence': 309,
    'summary': 'AI-powered weekly timesheet summaries, highlights, and alerts',
    'description': """
AI Timesheet Summary powered by Ollama
=======================================
Generate weekly AI summaries of employee timesheets.

Features:
---------
* Automatic weekly summary generation for each employee
* AI-powered highlights of key accomplishments
* Smart detection of overtime, underlogging, and anomalies
* Project-by-project hour breakdown
* Dashboard with team-wide statistics
* Weekly cron job for automated generation
* Multi-provider AI support via Ollama AI Base

Requirements:
-------------
* Ollama AI Base module (ollama_base)
* HR Timesheet module (hr_timesheet)
* Project module (project)
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 79,
    'currency': 'EUR',
    'depends': [
        'ollama_base',
        'hr_timesheet',
        'project',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/timesheet_cron_data.xml',
        'views/timesheet_dashboard_views.xml',
        'views/timesheet_summary_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
