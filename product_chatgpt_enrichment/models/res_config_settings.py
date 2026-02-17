# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_enrichment_paused = fields.Boolean(
        string='Pipeline en pause',
        help="Mettre en pause toutes les collectes et enrichissements automatiques.",
    )
    ai_enrichment_queue_count = fields.Integer(
        string='Items in queue',
        readonly=True,
    )
    ai_enrichment_done_count = fields.Integer(
        string='Completed',
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        config = self._get_ai_config()
        if config:
            res['ai_enrichment_paused'] = config.enrichment_paused
        # Queue stats
        Queue = self.env['product.enrichment.queue']
        res['ai_enrichment_queue_count'] = Queue.search_count([
            ('state', 'in', ('pending', 'collecting', 'collected', 'enriching')),
        ])
        res['ai_enrichment_done_count'] = Queue.search_count([
            ('state', '=', 'done'),
        ])
        return res

    def set_values(self):
        super().set_values()
        config = self._get_ai_config()
        if config:
            config.sudo().write({
                'enrichment_paused': self.ai_enrichment_paused,
            })

    def action_ai_start_pipeline(self):
        """Start (resume) the AI enrichment pipeline."""
        config = self._get_ai_config()
        if not config:
            raise UserError(_("No SearXNG/AI configuration found. Configure a provider first."))
        config.sudo().write({'enrichment_paused': False})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Pipeline started'),
                'message': _('AI enrichment pipeline is now running.'),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_ai_stop_pipeline(self):
        """Stop (pause) the AI enrichment pipeline."""
        config = self._get_ai_config()
        if not config:
            raise UserError(_("No SearXNG/AI configuration found."))
        config.sudo().write({'enrichment_paused': True})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Pipeline stopped'),
                'message': _('AI enrichment pipeline is paused. No automatic processing.'),
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_ai_open_queue(self):
        """Open the enrichment queue list view."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Enrichment Queue'),
            'res_model': 'product.enrichment.queue',
            'view_mode': 'list,form,graph,pivot',
            'target': 'current',
        }

    def action_ai_open_config(self):
        """Open the AI provider configuration."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Providers'),
            'res_model': 'chatgpt.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    @api.model
    def _get_ai_config(self):
        """Get the SearXNG-enabled AI config (or default)."""
        try:
            return self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            # Fallback: get any active config
            config = self.env['chatgpt.config'].search([
                ('active', '=', True),
            ], limit=1, order='is_default desc')
            return config or False
