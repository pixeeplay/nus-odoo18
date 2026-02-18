# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BankReconciliationDashboard(models.TransientModel):
    _name = 'bank.reconciliation.dashboard'
    _description = 'AI Bank Reconciliation Dashboard'

    # ------------------------------------------------------------------
    # Statistic fields
    # ------------------------------------------------------------------
    total_unreconciled = fields.Integer(
        string='Total Unreconciled', readonly=True,
    )
    ai_analyzed = fields.Integer(
        string='AI Analyzed', readonly=True,
    )
    ai_matched = fields.Integer(
        string='AI Matched', readonly=True,
        help='Lines where AI found a match suggestion.',
    )
    pending_review = fields.Integer(
        string='Pending Review', readonly=True,
        help='Lines analyzed by AI but not yet reconciled.',
    )
    avg_confidence = fields.Float(
        string='Avg Confidence', digits=(5, 1), readonly=True,
    )
    ai_provider = fields.Char(string='AI Provider', readonly=True)
    ai_model = fields.Char(string='AI Model', readonly=True)

    # ------------------------------------------------------------------
    # Default values: compute stats from statement lines
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        Line = self.env['account.bank.statement.line']

        # Unreconciled lines: not fully reconciled
        unreconciled = Line.search([
            ('is_reconciled', '=', False),
        ])
        total_unreconciled = len(unreconciled)

        # AI analyzed lines (among unreconciled)
        analyzed = unreconciled.filtered(lambda l: l.ai_analysis_date)
        ai_analyzed = len(analyzed)

        # AI matched: analyzed with a match suggestion that is not empty/no-match
        matched = analyzed.filtered(
            lambda l: l.ai_match_suggestion
            and l.ai_match_suggestion.lower() not in ('', 'no match found', 'no match', 'n/a')
        )
        ai_matched = len(matched)

        # Pending review: analyzed but still unreconciled
        pending_review = ai_analyzed  # all analyzed unreconciled are pending

        # Average confidence
        avg_confidence = 0.0
        if analyzed:
            confidences = analyzed.mapped('ai_match_confidence')
            avg_confidence = sum(confidences) / len(confidences)

        # AI config info
        provider_name = ''
        model_name = ''
        try:
            config = self.env['ollama.config'].get_active_config()
            provider_name = config.get_provider_display()
            model_name = config._get_model_name()
        except UserError:
            provider_name = _('Not Configured')
            model_name = _('N/A')

        res.update({
            'total_unreconciled': total_unreconciled,
            'ai_analyzed': ai_analyzed,
            'ai_matched': ai_matched,
            'pending_review': pending_review,
            'avg_confidence': avg_confidence,
            'ai_provider': provider_name,
            'ai_model': model_name,
        })
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_analyze_all(self):
        """Analyze all unreconciled, un-analyzed statement lines with AI."""
        self.ensure_one()
        Line = self.env['account.bank.statement.line']

        unanalyzed = Line.search([
            ('is_reconciled', '=', False),
            ('ai_analysis_date', '=', False),
        ], limit=50)

        if not unanalyzed:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nothing to Analyze'),
                    'message': _('All unreconciled lines have already been analyzed.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for line in unanalyzed:
            try:
                line.action_analyze_line()
                success += 1
            except Exception as e:
                _logger.warning("Failed to analyze statement line %s: %s", line.id, e)
                errors += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Analysis Complete'),
                'message': _('Analyzed %d lines. Errors: %d.') % (success, errors),
                'type': 'success' if errors == 0 else 'warning',
                'sticky': True,
            },
        }

    def action_open_unreconciled(self):
        """Open a list of all unreconciled statement lines."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Unreconciled Statement Lines'),
            'res_model': 'account.bank.statement.line',
            'view_mode': 'list,form',
            'domain': [('is_reconciled', '=', False)],
            'target': 'current',
        }

    def action_apply_suggestions(self):
        """Open analyzed lines with high-confidence AI matches for review."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Suggested Matches'),
            'res_model': 'account.bank.statement.line',
            'view_mode': 'list,form',
            'domain': [
                ('is_reconciled', '=', False),
                ('ai_match_suggestion', '!=', False),
                ('ai_match_confidence', '>=', 50.0),
            ],
            'target': 'current',
        }

    def action_open_config(self):
        """Open AI configuration settings."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Configuration'),
            'res_model': 'ollama.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def _refresh(self):
        """Refresh the dashboard by reloading the form."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Bank Reconciliation Dashboard'),
            'res_model': 'bank.reconciliation.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }

    def action_refresh(self):
        """Public refresh action for the button."""
        return self._refresh()
