# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CvAnalyzerDashboard(models.TransientModel):
    _name = 'cv.analyzer.dashboard'
    _description = 'CV Analyzer Dashboard'

    # ------------------------------------------------------------------
    # Statistic fields
    # ------------------------------------------------------------------
    total_applicants = fields.Integer(
        string='Total Applicants', readonly=True,
    )
    analyzed_applicants = fields.Integer(
        string='Analyzed', readonly=True,
    )
    unanalyzed_applicants = fields.Integer(
        string='Pending Analysis', readonly=True,
    )
    avg_score = fields.Integer(
        string='Average Score', readonly=True,
    )
    top_candidates = fields.Integer(
        string='Top Candidates (>80)', readonly=True,
    )
    medium_candidates = fields.Integer(
        string='Medium Candidates (40-80)', readonly=True,
    )
    weak_candidates = fields.Integer(
        string='Weak Candidates (<40)', readonly=True,
    )
    ai_provider = fields.Char(
        string='AI Provider', readonly=True,
    )
    ai_model = fields.Char(
        string='AI Model', readonly=True,
    )

    # ------------------------------------------------------------------
    # Default values: compute stats from hr.applicant
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        Applicant = self.env['hr.applicant']

        # All active applicants
        all_applicants = Applicant.search([('active', '=', True)])
        total = len(all_applicants)

        # Analyzed = those with a score > 0
        analyzed = all_applicants.filtered(lambda a: a.ai_cv_score > 0)
        analyzed_count = len(analyzed)
        unanalyzed_count = total - analyzed_count

        # Average score
        avg = 0
        if analyzed:
            avg = int(sum(analyzed.mapped('ai_cv_score')) / analyzed_count)

        # Candidate breakdown
        top = len(analyzed.filtered(lambda a: a.ai_cv_score > 80))
        medium = len(analyzed.filtered(
            lambda a: 40 <= a.ai_cv_score <= 80
        ))
        weak = len(analyzed.filtered(
            lambda a: 0 < a.ai_cv_score < 40
        ))

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
            'total_applicants': total,
            'analyzed_applicants': analyzed_count,
            'unanalyzed_applicants': unanalyzed_count,
            'avg_score': avg,
            'top_candidates': top,
            'medium_candidates': medium,
            'weak_candidates': weak,
            'ai_provider': provider_name,
            'ai_model': model_name,
        })
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_analyze_pending(self):
        """Analyze all unanalyzed applicants (batch, up to 50)."""
        self.ensure_one()
        Applicant = self.env['hr.applicant']

        unanalyzed = Applicant.search([
            ('ai_cv_score', '=', 0),
            ('active', '=', True),
        ], limit=50)

        if not unanalyzed:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('All Analyzed'),
                    'message': _('No pending applicants to analyze.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for applicant in unanalyzed:
            try:
                applicant.action_analyze_cv()
                success += 1
            except Exception as e:
                _logger.warning(
                    "Failed to analyze applicant %s: %s", applicant.id, e
                )
                errors += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Analysis Complete'),
                'message': _(
                    'Analyzed %d applicants. Errors: %d.'
                ) % (success, errors),
                'type': 'success' if errors == 0 else 'warning',
                'sticky': True,
            },
        }

    def action_open_top(self):
        """Open a list of top candidates (score > 80)."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Top Candidates (AI Score > 80)'),
            'res_model': 'hr.applicant',
            'view_mode': 'list,form',
            'domain': [('ai_cv_score', '>', 80), ('active', '=', True)],
            'target': 'current',
        }

    def action_open_unanalyzed(self):
        """Open a list of unanalyzed applicants."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Unanalyzed Applicants'),
            'res_model': 'hr.applicant',
            'view_mode': 'list,form',
            'domain': [
                ('ai_cv_score', '=', 0),
                ('active', '=', True),
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

    def action_refresh(self):
        """Refresh the dashboard by reloading the form."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI CV Analyzer Dashboard'),
            'res_model': 'cv.analyzer.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
