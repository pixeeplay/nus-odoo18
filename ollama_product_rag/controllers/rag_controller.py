# -*- coding: utf-8 -*-
import uuid
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class RagController(http.Controller):
    """JSON API endpoint for the RAG product assistant.

    Usage (from JavaScript / fetch):
        POST /ollama/rag/ask
        Content-Type: application/json
        Body: {"jsonrpc": "2.0", "params": {"question": "...", "session_id": "..."}}

    Response:
        {"jsonrpc": "2.0", "result": {"session_id": "...", "answer": "..."}}
    """

    @http.route('/ollama/rag/ask', type='json', auth='public', methods=['POST'], csrf=False)
    def ask_question(self, session_id=None, question='', **kwargs):
        """Handle a chatbot question via the JSON API."""
        if not question or not question.strip():
            return {'session_id': session_id or '', 'answer': 'Please provide a question.'}

        Conversation = request.env['product.rag.conversation'].sudo()

        # Find or create conversation by session_id
        conv = None
        if session_id:
            conv = Conversation.search([('session_id', '=', session_id)], limit=1)

        if not conv:
            if not session_id:
                session_id = str(uuid.uuid4())
            conv = Conversation.create({
                'session_id': session_id,
                'partner_id': request.env.user.partner_id.id if not request.env.user._is_public() else False,
            })

        try:
            answer = conv.ask_question(question)
        except Exception as e:
            _logger.exception("RAG API error: %s", e)
            answer = "Sorry, an error occurred while processing your question. Please try again."

        return {
            'session_id': conv.session_id or session_id,
            'answer': answer,
        }
