# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import models, fields, api, _


class EmailComposerDashboard(models.TransientModel):
    _name = 'email.composer.dashboard'
    _description = 'AI Email Composer Dashboard'

    total_generated = fields.Integer('Total Generated', readonly=True)
    generated_today = fields.Integer('Today', readonly=True)
    generated_week = fields.Integer('This Week', readonly=True)
    by_professional = fields.Integer('Professional', readonly=True)
    by_friendly = fields.Integer('Friendly', readonly=True)
    by_formal = fields.Integer('Formal', readonly=True)
    by_persuasive = fields.Integer('Persuasive', readonly=True)
    by_apology = fields.Integer('Apology', readonly=True)
    total_templates = fields.Integer('Templates', readonly=True)
    ai_provider = fields.Char('AI Provider', readonly=True)
    ai_model = fields.Char('AI Model', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Log = self.env['ollama.log'].sudo()
        domain = [('res_model', '=', 'email.compose.ai.wizard')]

        res['total_generated'] = Log.search_count(domain + [('status', '=', 'success')])

        today_start = fields.Datetime.now().replace(hour=0, minute=0, second=0)
        res['generated_today'] = Log.search_count(
            domain + [('status', '=', 'success'), ('create_date', '>=', today_start)])

        week_start = fields.Datetime.now() - timedelta(days=7)
        res['generated_week'] = Log.search_count(
            domain + [('status', '=', 'success'), ('create_date', '>=', week_start)])

        # Tone breakdown — stored in prompt_preview field as prefix
        for tone_key, field_name in [
            ('professional', 'by_professional'),
            ('friendly', 'by_friendly'),
            ('formal', 'by_formal'),
            ('persuasive', 'by_persuasive'),
            ('apology', 'by_apology'),
        ]:
            res[field_name] = Log.search_count(
                domain + [
                    ('status', '=', 'success'),
                    ('prompt_preview', 'like', f'[{tone_key}]'),
                ])

        res['total_templates'] = self.env['ollama.email.template'].search_count([])

        # Active AI config
        try:
            config = self.env['ollama.config'].get_active_config()
            res['ai_provider'] = config.get_provider_display()
            res['ai_model'] = config._get_model_name()
        except Exception:
            res['ai_provider'] = 'Not configured'
            res['ai_model'] = '-'

        return res

    def action_open_templates(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Email Templates'),
            'res_model': 'ollama.email.template',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_open_logs(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Email AI Logs'),
            'res_model': 'ollama.log',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', 'email.compose.ai.wizard')],
            'target': 'current',
        }

    def action_open_config(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Configuration'),
            'res_model': 'ollama.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def _refresh(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Email Composer Dashboard'),
            'res_model': 'email.composer.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
