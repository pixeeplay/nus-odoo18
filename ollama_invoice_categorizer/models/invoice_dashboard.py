# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class InvoiceCategorizerDashboard(models.TransientModel):
    _name = 'invoice.categorizer.dashboard'
    _description = 'AI Invoice Categorizer Dashboard'

    # ------------------------------------------------------------------
    # Statistics fields
    # ------------------------------------------------------------------
    total_vendor_bills = fields.Integer('Total Vendor Bills', readonly=True)
    analyzed = fields.Integer('Analyzed', readonly=True)
    anomalies_found = fields.Integer('Anomalies Found', readonly=True)
    auto_categorized = fields.Integer('Auto-Categorized', readonly=True)
    avg_confidence = fields.Float('Avg Confidence (%)', digits=(5, 1), readonly=True)
    pending_review = fields.Integer('Pending Review', readonly=True)
    ai_provider = fields.Char('AI Provider', readonly=True)
    ai_model = fields.Char('AI Model', readonly=True)

    # ------------------------------------------------------------------
    # Default values -- compute stats
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Move = self.env['account.move'].sudo()

        # Base domain: vendor bills in draft or posted
        base_domain = [
            ('move_type', '=', 'in_invoice'),
            ('state', 'in', ['draft', 'posted']),
        ]

        total = Move.search_count(base_domain)

        analyzed = Move.search_count(base_domain + [
            ('ai_analysis_date', '!=', False),
        ])

        anomalies = Move.search_count(base_domain + [
            ('ai_anomaly_detected', '=', True),
        ])

        categorized = Move.search_count(base_domain + [
            ('ai_expense_category', '!=', False),
            ('ai_expense_category', '!=', ''),
        ])

        pending = total - analyzed

        # Average confidence
        analyzed_moves = Move.search(base_domain + [
            ('ai_analysis_date', '!=', False),
            ('ai_account_confidence', '>', 0),
        ])
        avg_conf = 0.0
        if analyzed_moves:
            total_conf = sum(m.ai_account_confidence for m in analyzed_moves)
            avg_conf = total_conf / len(analyzed_moves)

        res.update({
            'total_vendor_bills': total,
            'analyzed': analyzed,
            'anomalies_found': anomalies,
            'auto_categorized': categorized,
            'avg_confidence': round(avg_conf, 1),
            'pending_review': pending,
        })

        # Active AI config info
        try:
            config = self.env['ollama.config'].get_active_config()
            res['ai_provider'] = config.get_provider_display()
            res['ai_model'] = config._get_model_name()
        except Exception:
            res['ai_provider'] = 'Not configured'
            res['ai_model'] = '-'

        return res

    # ------------------------------------------------------------------
    # Dashboard Actions
    # ------------------------------------------------------------------
    def action_analyze_pending(self):
        """Batch-analyze all vendor bills that have not been analyzed yet."""
        moves = self.env['account.move'].search([
            ('move_type', '=', 'in_invoice'),
            ('state', 'in', ['draft', 'posted']),
            ('ai_analysis_date', '=', False),
        ])

        if not moves:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nothing to Analyze'),
                    'message': _('All vendor bills have already been analyzed.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for move in moves:
            try:
                move.action_analyze_invoice()
                if move.ai_analysis_date:
                    success += 1
                else:
                    errors += 1
            except Exception as e:
                _logger.error(
                    "Batch invoice analysis error for move %s: %s", move.id, e)
                errors += 1

        msg = _('%d invoices analyzed successfully.') % success
        if errors:
            msg += _(' %d errors.') % errors

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Analysis Complete'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    def action_open_anomalies(self):
        """Open list of vendor bills where an anomaly was detected."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoices with Anomalies'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [
                ('move_type', '=', 'in_invoice'),
                ('ai_anomaly_detected', '=', True),
            ],
            'target': 'current',
        }

    def action_apply_all(self):
        """Apply AI account suggestions to all analyzed invoices that have
        a suggestion but have not been applied yet."""
        moves = self.env['account.move'].search([
            ('move_type', '=', 'in_invoice'),
            ('state', 'in', ['draft', 'posted']),
            ('ai_account_suggestion', '!=', False),
            ('ai_account_suggestion', '!=', ''),
            ('ai_account_confidence', '>=', 70),
        ])

        if not moves:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nothing to Apply'),
                    'message': _('No high-confidence suggestions to apply.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for move in moves:
            try:
                result = move.action_apply_suggestion()
                params = result.get('params', {})
                if params.get('type') == 'success':
                    success += 1
                else:
                    errors += 1
            except Exception as e:
                _logger.error(
                    "Batch apply error for move %s: %s", move.id, e)
                errors += 1

        msg = _('%d suggestions applied successfully.') % success
        if errors:
            msg += _(' %d could not be applied.') % errors

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Apply Complete'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    def action_open_config(self):
        """Open AI configuration."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Configuration'),
            'res_model': 'ollama.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def _refresh(self):
        """Reload the dashboard."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Invoice Categorizer'),
            'res_model': 'invoice.categorizer.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
