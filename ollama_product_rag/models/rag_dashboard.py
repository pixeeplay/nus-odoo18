# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class RagDashboard(models.TransientModel):
    _name = 'rag.dashboard'
    _description = 'RAG Dashboard'

    # --- Stats ---
    total_indexed = fields.Integer(string='Indexed Products', readonly=True)
    total_products = fields.Integer(string='Total Products', readonly=True)
    unindexed = fields.Integer(string='Unindexed Products', readonly=True)
    total_conversations = fields.Integer(string='Conversations', readonly=True)
    total_messages = fields.Integer(string='Total Messages', readonly=True)
    messages_today = fields.Integer(string='Messages Today', readonly=True)
    avg_messages_per_conv = fields.Float(string='Avg Messages/Conv', readonly=True, digits=(10, 1))

    # --- Test chatbot ---
    test_question = fields.Text(string='Test Question')
    test_answer = fields.Text(string='AI Answer', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res.update(self._compute_stats())
        return res

    @api.model
    def _compute_stats(self):
        """Compute all dashboard statistics."""
        RagIndex = self.env['product.rag.index']
        Product = self.env['product.template']
        Conversation = self.env['product.rag.conversation']
        Message = self.env['product.rag.message']

        total_indexed = RagIndex.search_count([])
        total_products = Product.search_count([])
        total_conversations = Conversation.search_count([])
        total_messages = Message.search_count([])

        # Messages today
        today_start = fields.Datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        messages_today = Message.search_count([('create_date', '>=', today_start)])

        # Average messages per conversation
        avg = 0.0
        if total_conversations > 0:
            avg = round(total_messages / total_conversations, 1)

        return {
            'total_indexed': total_indexed,
            'total_products': total_products,
            'unindexed': max(0, total_products - total_indexed),
            'total_conversations': total_conversations,
            'total_messages': total_messages,
            'messages_today': messages_today,
            'avg_messages_per_conv': avg,
        }

    # --- Actions ---
    def action_reindex(self):
        """Trigger a full catalog reindex."""
        self.ensure_one()
        self.env['product.rag.index'].reindex_catalog()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Reindex Complete'),
                'message': _('Product catalog has been reindexed successfully.'),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_open_conversations(self):
        """Open the conversation list view."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('RAG Conversations'),
            'res_model': 'product.rag.conversation',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_open_index(self):
        """Open the index list view."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Product Index'),
            'res_model': 'product.rag.index',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_test_chatbot(self):
        """Send a test question to the RAG pipeline."""
        self.ensure_one()
        if not self.test_question or not self.test_question.strip():
            raise UserError(_("Please type a question first."))

        # Create or find a test conversation
        Conversation = self.env['product.rag.conversation']
        conv = Conversation.search([('session_id', '=', 'dashboard-test')], limit=1)
        if not conv:
            conv = Conversation.create({'session_id': 'dashboard-test'})

        answer = conv.ask_question(self.test_question)
        self.test_answer = answer

        # Return the same wizard to show the answer
        return {
            'type': 'ir.actions.act_window',
            'name': _('Test Chatbot'),
            'res_model': 'rag.dashboard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_refresh(self):
        """Refresh dashboard stats."""
        self.ensure_one()
        stats = self._compute_stats()
        self.write(stats)
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Product RAG Dashboard'),
            'res_model': 'rag.dashboard',
            'view_mode': 'form',
            'target': 'inline',
        }
