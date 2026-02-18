# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CrmAiDashboard(models.TransientModel):
    _name = 'crm.ai.dashboard'
    _description = 'CRM AI Dashboard'

    # ------------------------------------------------------------------
    # Statistic fields
    # ------------------------------------------------------------------
    total_leads = fields.Integer(string='Total Active Leads', readonly=True)
    scored_leads = fields.Integer(string='Scored Leads', readonly=True)
    unscored_leads = fields.Integer(string='Unscored Leads', readonly=True)
    avg_score = fields.Integer(string='Average Score', readonly=True)
    hot_leads = fields.Integer(string='Hot Leads (>70)', readonly=True)
    warm_leads = fields.Integer(string='Warm Leads (40-70)', readonly=True)
    cold_leads = fields.Integer(string='Cold Leads (<40)', readonly=True)
    ai_provider = fields.Char(string='AI Provider', readonly=True)
    ai_model = fields.Char(string='AI Model', readonly=True)

    # ------------------------------------------------------------------
    # Default values: compute stats from crm.lead
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        Lead = self.env['crm.lead']

        # Exclude won and lost stages
        domain = [
            ('stage_id.is_won', '=', False),
            ('active', '=', True),
        ]

        all_leads = Lead.search(domain)
        total = len(all_leads)
        scored = all_leads.filtered(lambda l: l.ai_score > 0)
        scored_count = len(scored)
        unscored_count = total - scored_count

        # Compute average score
        avg = 0
        if scored:
            avg = int(sum(scored.mapped('ai_score')) / scored_count)

        # Lead temperature breakdown
        hot = len(scored.filtered(lambda l: l.ai_score > 70))
        warm = len(scored.filtered(lambda l: 40 <= l.ai_score <= 70))
        cold = len(scored.filtered(lambda l: 0 < l.ai_score < 40))

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
            'total_leads': total,
            'scored_leads': scored_count,
            'unscored_leads': unscored_count,
            'avg_score': avg,
            'hot_leads': hot,
            'warm_leads': warm,
            'cold_leads': cold,
            'ai_provider': provider_name,
            'ai_model': model_name,
        })
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_score_all(self):
        """Score all unscored active leads (excluding won/lost)."""
        self.ensure_one()
        Lead = self.env['crm.lead']

        unscored = Lead.search([
            ('ai_score', '=', 0),
            ('stage_id.is_won', '=', False),
            ('active', '=', True),
        ], limit=50)

        if not unscored:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('All Leads Scored'),
                    'message': _('No unscored leads found.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for lead in unscored:
            try:
                lead.action_score_lead()
                success += 1
            except Exception as e:
                _logger.warning("Failed to score lead %s: %s", lead.id, e)
                errors += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Scoring Complete'),
                'message': _('Scored %d leads. Errors: %d.') % (success, errors),
                'type': 'success' if errors == 0 else 'warning',
                'sticky': True,
            },
        }

    def action_open_hot_leads(self):
        """Open a list of hot leads (score > 70)."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Hot Leads (AI Score > 70)'),
            'res_model': 'crm.lead',
            'view_mode': 'list,form',
            'domain': [('ai_score', '>', 70), ('active', '=', True)],
            'context': {'default_type': 'lead'},
            'target': 'current',
        }

    def action_open_unscored(self):
        """Open a list of unscored leads."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Unscored Leads'),
            'res_model': 'crm.lead',
            'view_mode': 'list,form',
            'domain': [
                ('ai_score', '=', 0),
                ('stage_id.is_won', '=', False),
                ('active', '=', True),
            ],
            'context': {'default_type': 'lead'},
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

    def action_refresh(self):
        """Refresh the dashboard by reloading the form."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI CRM Dashboard'),
            'res_model': 'crm.ai.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
