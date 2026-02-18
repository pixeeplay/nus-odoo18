# -*- coding: utf-8 -*-
{
    'name': 'AI Product Assistant (RAG) powered by Ollama',
    'version': '19.0.1.0.0',
    'category': 'Website/Website',
    'sequence': 312,
    'summary': 'AI-powered virtual product assistant with RAG — conversational product search',
    'description': """
AI Product Assistant (RAG)
==========================
Retrieval-Augmented Generation for your product catalog.

Features
--------
* Indexes your entire product catalog for AI-powered search
* Conversational product assistant — customers ask questions, AI answers with real products
* Full conversation history with referenced products
* Dashboard with indexing stats and quick actions
* Nightly cron for automatic catalog re-indexing
* JSON API endpoint for website chatbot integration
* Works with all providers: Ollama, OpenAI, Gemini, Anthropic, Llama.cpp

How it works
------------
1. Products are indexed into a searchable text format (name, description, price, category)
2. When a user asks a question, relevant products are retrieved via text search
3. The AI receives the question + product context and generates a helpful answer
4. Conversation history is maintained for multi-turn dialogues
    """,
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'price': 199,
    'currency': 'EUR',
    'depends': [
        'ollama_base',
        'product',
        'website_sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/rag_cron_data.xml',
        'views/rag_dashboard_views.xml',
        'views/rag_conversation_views.xml',
        'views/rag_index_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
