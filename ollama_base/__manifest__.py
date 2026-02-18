{
    'name': 'Ollama AI Base',
    'version': '18.0.1.0.0',
    'category': 'Technical',
    'sequence': 300,
    'summary': 'Multi-provider AI engine for Odoo — Ollama, OpenAI, Gemini, Anthropic, Llama.cpp',
    'description': """
Ollama AI Base
==============
Foundation module for the Ollama AI Suite for Odoo.

Provides a unified AI configuration and dispatcher for all AI-powered modules.

Features:
---------
* Multi-provider support: Ollama, OpenAI, Google Gemini, Anthropic Claude, Perplexity, Llama.cpp
* Centralized configuration: API keys, URLs, models, tuning parameters
* Ollama-specific tuning: context window, GPU layers, keep-alive, timeout
* Model auto-discovery for Ollama and Llama.cpp
* Connection testing and diagnostics
* AI call logging with automatic cleanup
* Thread-safe HTTP caller for parallel processing
* Reusable mixin for all downstream modules

Requirements:
-------------
* Ollama server (local or remote) — https://ollama.com
* OR any supported cloud provider API key
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'LGPL-3',
    'price': 0,
    'currency': 'EUR',
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'data/ollama_config_data.xml',
        'data/ir_cron_data.xml',
        'views/ollama_config_views.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
