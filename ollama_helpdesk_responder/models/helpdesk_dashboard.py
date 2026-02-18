# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class HelpdeskAIDashboard(models.TransientModel):
    _name = 'helpdesk.ai.dashboard'
    _description = 'Helpdesk AI Dashboard'

    # ------------------------------------------------------------------
    # Statistics fields
    # ------------------------------------------------------------------
    total_open = fields.Integer(string='Open Tickets', readonly=True)
    total_closed = fields.Integer(string='Closed Tickets', readonly=True)
    ai_classified = fields.Integer(string='AI Classified', readonly=True)
    ai_responded = fields.Integer(string='AI Responded', readonly=True)
    positive_sentiment = fields.Integer(string='Positive Sentiment', readonly=True)
    negative_sentiment = fields.Integer(string='Negative Sentiment', readonly=True)
    neutral_sentiment = fields.Integer(string='Neutral Sentiment', readonly=True)
    unassigned = fields.Integer(string='Unassigned Tickets', readonly=True)

    # ------------------------------------------------------------------
    # Default values (compute stats on load)
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Ticket = self.env['ollama.helpdesk.ticket']

        closing_stages = self.env['ollama.helpdesk.stage'].search([
            ('is_closing', '=', True),
        ])
        closing_ids = closing_stages.ids

        all_tickets = Ticket.search([])
        open_tickets = all_tickets.filtered(lambda t: t.stage_id.id not in closing_ids)
        closed_tickets = all_tickets.filtered(lambda t: t.stage_id.id in closing_ids)

        res.update({
            'total_open': len(open_tickets),
            'total_closed': len(closed_tickets),
            'ai_classified': Ticket.search_count([
                ('ai_classification', '!=', False),
            ]),
            'ai_responded': Ticket.search_count([
                ('ai_draft_response', '!=', False),
            ]),
            'positive_sentiment': Ticket.search_count([
                ('ai_sentiment', '=', 'positive'),
            ]),
            'negative_sentiment': Ticket.search_count([
                ('ai_sentiment', '=', 'negative'),
            ]),
            'neutral_sentiment': Ticket.search_count([
                ('ai_sentiment', '=', 'neutral'),
            ]),
            'unassigned': Ticket.search_count([
                ('user_id', '=', False),
                ('stage_id', 'not in', closing_ids),
            ]),
        })
        return res

    # ------------------------------------------------------------------
    # Dashboard actions
    # ------------------------------------------------------------------
    def action_classify_all(self):
        """Classify all open tickets that have not been classified yet."""
        closing_stages = self.env['ollama.helpdesk.stage'].search([
            ('is_closing', '=', True),
        ])
        tickets = self.env['ollama.helpdesk.ticket'].search([
            ('ai_classification', '=', False),
            ('stage_id', 'not in', closing_stages.ids),
        ])
        classified = 0
        errors = 0
        for ticket in tickets:
            try:
                ticket.action_classify_ticket()
                classified += 1
            except Exception as e:
                _logger.warning("Failed to classify ticket %s: %s", ticket.id, e)
                errors += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Classification Complete'),
                'message': _('%d tickets classified, %d errors.') % (classified, errors),
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            }
        }

    def action_open_unclassified(self):
        """Open a list of tickets without AI classification."""
        closing_stages = self.env['ollama.helpdesk.stage'].search([
            ('is_closing', '=', True),
        ])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Unclassified Tickets'),
            'res_model': 'ollama.helpdesk.ticket',
            'view_mode': 'list,form',
            'domain': [
                ('ai_classification', '=', False),
                ('stage_id', 'not in', closing_stages.ids),
            ],
            'context': {'default_stage_id': False},
        }

    def action_open_negative(self):
        """Open a list of tickets with negative sentiment."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Negative Sentiment Tickets'),
            'res_model': 'ollama.helpdesk.ticket',
            'view_mode': 'list,form',
            'domain': [('ai_sentiment', '=', 'negative')],
        }

    def action_open_config(self):
        """Open the Ollama AI configuration."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Configuration'),
            'res_model': 'ollama.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_refresh(self):
        """Refresh the dashboard by reloading."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Helpdesk Dashboard'),
            'res_model': 'helpdesk.ai.dashboard',
            'view_mode': 'form',
            'target': 'current',
            'flags': {'initial_mode': 'edit'},
        }

    def action_open_all_tickets(self):
        """Open all tickets in kanban view."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('All Tickets'),
            'res_model': 'ollama.helpdesk.ticket',
            'view_mode': 'kanban,list,form',
        }

    def action_open_open_tickets(self):
        """Open only non-closed tickets."""
        closing_stages = self.env['ollama.helpdesk.stage'].search([
            ('is_closing', '=', True),
        ])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Open Tickets'),
            'res_model': 'ollama.helpdesk.ticket',
            'view_mode': 'kanban,list,form',
            'domain': [('stage_id', 'not in', closing_stages.ids)],
        }

    def action_open_unassigned(self):
        """Open unassigned tickets."""
        closing_stages = self.env['ollama.helpdesk.stage'].search([
            ('is_closing', '=', True),
        ])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Unassigned Tickets'),
            'res_model': 'ollama.helpdesk.ticket',
            'view_mode': 'list,form',
            'domain': [
                ('user_id', '=', False),
                ('stage_id', 'not in', closing_stages.ids),
            ],
        }
