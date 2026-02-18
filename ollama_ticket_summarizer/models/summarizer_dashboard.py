# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SummarizerDashboard(models.TransientModel):
    _name = 'summarizer.dashboard'
    _description = 'AI Summarizer Dashboard'

    # ------------------------------------------------------------------
    # Statistic Fields
    # ------------------------------------------------------------------
    total_summaries = fields.Integer(
        string='Total Summaries',
        readonly=True,
    )
    generated_week = fields.Integer(
        string='Generated This Week',
        readonly=True,
    )
    long_threads = fields.Integer(
        string='Long Threads (>10 msgs)',
        readonly=True,
    )
    escalations = fields.Integer(
        string='Escalations Detected',
        readonly=True,
    )
    avg_messages = fields.Float(
        string='Avg Messages / Summary',
        readonly=True,
        digits=(12, 1),
    )

    # ------------------------------------------------------------------
    # Default Get — Compute Stats
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Summary = self.env['ticket.ai.summary']

        # Total summaries
        total = Summary.search_count([])

        # Generated this week
        week_ago = fields.Datetime.now() - timedelta(days=7)
        week_count = Summary.search_count([
            ('create_date', '>=', week_ago),
        ])

        # Escalations
        escalation_count = Summary.search_count([
            ('ai_escalation_needed', '=', True),
            ('state', '=', 'done'),
        ])

        # Average messages per summary
        summaries = Summary.search([('state', '=', 'done'), ('message_count', '>', 0)])
        avg_msg = 0.0
        if summaries:
            total_msgs = sum(summaries.mapped('message_count'))
            avg_msg = round(total_msgs / len(summaries), 1)

        # Long threads: count distinct (model, res_id) pairs in mail.message
        # with more than 10 messages
        self.env.cr.execute("""
            SELECT COUNT(*) FROM (
                SELECT model, res_id
                FROM mail_message
                WHERE model IS NOT NULL
                  AND res_id IS NOT NULL
                  AND res_id > 0
                  AND message_type IN ('comment', 'email')
                GROUP BY model, res_id
                HAVING COUNT(*) > 10
            ) AS long_threads
        """)
        long_thread_count = self.env.cr.fetchone()[0] or 0

        res.update({
            'total_summaries': total,
            'generated_week': week_count,
            'long_threads': long_thread_count,
            'escalations': escalation_count,
            'avg_messages': avg_msg,
        })
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_summarize_long_threads(self):
        """Find all threads with >10 messages and create summaries for them."""
        self.env.cr.execute("""
            SELECT model, res_id, COUNT(*) as msg_count
            FROM mail_message
            WHERE model IS NOT NULL
              AND res_id IS NOT NULL
              AND res_id > 0
              AND message_type IN ('comment', 'email')
            GROUP BY model, res_id
            HAVING COUNT(*) > 10
            ORDER BY msg_count DESC
        """)
        rows = self.env.cr.fetchall()

        if not rows:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Long Threads'),
                    'message': _('No threads with more than 10 messages found.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        Summary = self.env['ticket.ai.summary']
        success = 0
        errors = 0
        skipped = 0

        for model_name, res_id, _count in rows:
            # Skip if already summarized
            existing = Summary.search([
                ('res_model', '=', model_name),
                ('res_id', '=', res_id),
                ('state', '=', 'done'),
            ], limit=1)
            if existing:
                skipped += 1
                continue

            try:
                Summary.action_summarize_record(model_name, res_id)
                success += 1
            except Exception as e:
                errors += 1
                _logger.warning(
                    'Summary generation failed for %s/%s: %s',
                    model_name, res_id, e,
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bulk Summarization Complete'),
                'message': _('%d summaries generated, %d skipped (already done), %d errors.') % (
                    success, skipped, errors),
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    def action_open_escalations(self):
        """Open list of summaries flagged for escalation."""
        return {
            'name': _('Escalations Detected'),
            'type': 'ir.actions.act_window',
            'res_model': 'ticket.ai.summary',
            'view_mode': 'list,form',
            'domain': [
                ('ai_escalation_needed', '=', True),
                ('state', '=', 'done'),
            ],
            'context': {},
        }

    def action_open_all(self):
        """Open all summaries."""
        return {
            'name': _('All AI Summaries'),
            'type': 'ir.actions.act_window',
            'res_model': 'ticket.ai.summary',
            'view_mode': 'list,form',
            'domain': [],
            'context': {},
        }

    def action_open_config(self):
        """Open the Ollama AI configuration."""
        return {
            'name': _('AI Configuration'),
            'type': 'ir.actions.act_window',
            'res_model': 'ollama.config',
            'view_mode': 'form',
            'target': 'current',
        }

    def action_refresh(self):
        """Refresh the dashboard by reloading the form view."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'summarizer.dashboard',
            'view_mode': 'form',
            'target': 'main',
        }
