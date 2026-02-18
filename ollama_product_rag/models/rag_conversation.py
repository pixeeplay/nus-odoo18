# -*- coding: utf-8 -*-
import logging
import re

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful product assistant for an online store. Your job is to help customers find the right products based on their questions and needs.

Rules:
- Answer based ONLY on the product information provided in the context below.
- If no matching products are found, say so politely and suggest the customer refine their search.
- Always mention product names and prices when recommending products.
- Be concise, friendly and helpful.
- If the customer asks something unrelated to products, politely redirect them to product-related questions.
- Format your response in plain text (no markdown, no HTML).
"""


class ProductRagConversation(models.Model):
    _name = 'product.rag.conversation'
    _description = 'RAG Conversation'
    _order = 'create_date desc'
    _inherit = ['ollama.mixin']

    session_id = fields.Char(
        string='Session ID',
        index=True,
        help='Unique identifier for this conversation session.',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        ondelete='set null',
    )
    message_ids = fields.One2many(
        'product.rag.message',
        'conversation_id',
        string='Messages',
    )
    create_date = fields.Datetime(
        string='Started',
        readonly=True,
    )
    message_count = fields.Integer(
        string='Messages',
        compute='_compute_message_count',
        store=True,
    )

    @api.depends('message_ids')
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    def name_get(self):
        result = []
        for rec in self:
            label = rec.session_id or f"Conv #{rec.id}"
            if rec.partner_id:
                label = f"{rec.partner_id.name} - {label}"
            result.append((rec.id, label))
        return result

    # ------------------------------------------------------------------
    # Core RAG pipeline
    # ------------------------------------------------------------------
    def ask_question(self, question):
        """Main RAG pipeline: search products, build context, call AI.

        :param question: User's question text
        :returns: Assistant response text (str)
        """
        self.ensure_one()

        if not question or not question.strip():
            return _("Please ask a question about our products.")

        # Step 1: Extract search keywords from the question
        keywords = self._extract_keywords(question)

        # Step 2: Search the product index
        RagIndex = self.env['product.rag.index']
        search_results = RagIndex.search_products(keywords, limit=5)

        # Step 3: Build the prompt with product context
        prompt = self._build_prompt(question, search_results)

        # Step 4: Call AI via the mixin
        _logger.info("RAG: Calling AI for conversation %s", self.session_id or self.id)
        answer = self._call_ollama_safe(
            prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=1500,
            temperature=0.3,
            log_model='product.rag.conversation',
            log_res_id=self.id,
        )

        if not answer:
            answer = _(
                "I apologize, but I'm unable to process your request right now. "
                "Please try again later or contact our support team."
            )

        # Step 5: Create message records
        referenced_products = [r['product_id'] for r in search_results if r.get('product_id')]

        self.env['product.rag.message'].create({
            'conversation_id': self.id,
            'role': 'user',
            'content': question,
        })
        self.env['product.rag.message'].create({
            'conversation_id': self.id,
            'role': 'assistant',
            'content': answer,
            'referenced_product_ids': [(6, 0, referenced_products)] if referenced_products else False,
        })

        return answer

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _extract_keywords(self, question):
        """Extract meaningful search keywords from the user's question."""
        # Remove common stop words and short words
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'can', 'shall',
            'it', 'its', 'this', 'that', 'these', 'those', 'i', 'me',
            'my', 'we', 'our', 'you', 'your', 'he', 'she', 'they',
            'them', 'what', 'which', 'who', 'whom', 'where', 'when',
            'why', 'how', 'not', 'no', 'nor', 'but', 'and', 'or',
            'if', 'then', 'so', 'too', 'very', 'just', 'about',
            'for', 'with', 'from', 'into', 'of', 'to', 'in', 'on',
            'at', 'by', 'up', 'out', 'off', 'over', 'under', 'again',
            'there', 'here', 'all', 'any', 'both', 'each', 'few',
            'more', 'most', 'some', 'such', 'than', 'also', 'get',
            'looking', 'need', 'want', 'like', 'find', 'search',
            'show', 'tell', 'give', 'help', 'please', 'thanks',
            'something', 'anything', 'thing', 'things',
        }
        words = re.split(r'[\s,;.!?\-/]+', question.lower())
        keywords = [w for w in words if len(w) >= 3 and w not in stop_words]
        # Return space-separated keywords, or original question if nothing left
        return ' '.join(keywords) if keywords else question.strip()

    def _build_prompt(self, question, search_results):
        """Build the full prompt with product context and conversation history."""
        parts = []

        # Conversation history (last 5 messages for context)
        recent_messages = self.message_ids.sorted('create_date')[-5:] if self.message_ids else []
        if recent_messages:
            parts.append("=== Previous conversation ===")
            for msg in recent_messages:
                role_label = "Customer" if msg.role == 'user' else "Assistant"
                parts.append(f"{role_label}: {msg.content}")
            parts.append("")

        # Product context from search results
        if search_results:
            parts.append("=== Matching products from our catalog ===")
            for i, result in enumerate(search_results, 1):
                parts.append(f"\n--- Product {i}: {result['product_name']} ---")
                parts.append(result.get('snippet', ''))
            parts.append("")
        else:
            parts.append("=== No matching products found in the catalog ===")
            parts.append("")

        # The actual question
        parts.append(f"=== Customer question ===")
        parts.append(question)

        return '\n'.join(parts)


class ProductRagMessage(models.Model):
    _name = 'product.rag.message'
    _description = 'RAG Conversation Message'
    _order = 'create_date'

    conversation_id = fields.Many2one(
        'product.rag.conversation',
        string='Conversation',
        required=True,
        ondelete='cascade',
        index=True,
    )
    role = fields.Selection(
        [('user', 'User'), ('assistant', 'Assistant')],
        string='Role',
        required=True,
    )
    content = fields.Text(
        string='Content',
        required=True,
    )
    referenced_product_ids = fields.Many2many(
        'product.template',
        'rag_message_product_rel',
        'message_id',
        'product_id',
        string='Referenced Products',
    )
    create_date = fields.Datetime(
        string='Sent',
        readonly=True,
    )
