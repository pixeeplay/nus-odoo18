# -*- coding: utf-8 -*-
{
    'name': 'AI CV Analyzer powered by Ollama',
    'version': '18.0.1.0.0',
    'category': 'Human Resources/Recruitment',
    'sequence': 308,
    'summary': 'AI-powered CV analysis, scoring, and interview question generation',
    'description': """
AI CV Analyzer powered by Ollama
=================================
AI-powered CV/resume analysis for recruitment.

Features:
---------
* AI-driven CV scoring (0-100) for job applicants
* Automatic strengths and weaknesses extraction
* Interview question generation based on CV content
* Recruitment dashboard with AI analytics
* Works with all AI providers supported by Ollama Base
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 129,
    'currency': 'EUR',
    'depends': [
        'ollama_base',
        'hr_recruitment',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/cv_dashboard_views.xml',
        'views/hr_applicant_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
    'external_dependencies': {'python': []},
}
