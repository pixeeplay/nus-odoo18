# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ProductEnrichmentDashboard(models.TransientModel):
    _name = 'product.enrichment.dashboard'
    _description = 'AI Enrichment Dashboard'

    # --- Stats (populated in default_get) ---
    total_pending = fields.Integer(string='En attente', readonly=True)
    total_collecting = fields.Integer(string='Collecte', readonly=True)
    total_collected = fields.Integer(string='Collecté', readonly=True)
    total_enriching = fields.Integer(string='Enrichissement', readonly=True)
    total_done = fields.Integer(string='Terminé', readonly=True)
    total_error = fields.Integer(string='Erreur', readonly=True)
    total_skipped = fields.Integer(string='Ignoré', readonly=True)
    total_all = fields.Integer(string='Total', readonly=True)
    success_rate = fields.Float(string='Taux de succès (%)', readonly=True)
    avg_time_search = fields.Float(string='Moy. SearXNG (s)', readonly=True)
    avg_time_ollama = fields.Float(string='Moy. Ollama (s)', readonly=True)
    confidence_high = fields.Integer(string='Confiance haute', readonly=True)
    confidence_medium = fields.Integer(string='Confiance moyenne', readonly=True)
    confidence_low = fields.Integer(string='Confiance basse', readonly=True)

    # --- Pipeline status ---
    pipeline_running = fields.Boolean(string='Pipeline actif', readonly=True)
    current_provider = fields.Char(string='Provider', readonly=True)
    current_model = fields.Char(string='Modèle', readonly=True)

    # --- Tuning: Ollama ---
    ollama_request_timeout = fields.Integer(string='Ollama Timeout (s)')
    ollama_num_ctx = fields.Integer(string='Context Window')
    ollama_num_gpu = fields.Integer(string='GPU Layers')
    ollama_keep_alive = fields.Char(string='Keep Alive')

    # --- Tuning: Workers & Batches ---
    cfg_ollama_parallel_workers = fields.Integer(string='Ollama Workers')
    cfg_searxng_parallel_workers = fields.Integer(string='SearXNG Workers')
    cfg_batch_size_collect = fields.Integer(string='Batch Collect')
    cfg_batch_size_enrich = fields.Integer(string='Batch Enrich')
    cfg_max_tokens = fields.Integer(string='Max Tokens')
    cfg_temperature = fields.Float(string='Temperature')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Queue = self.env['product.enrichment.queue']

        # Counts by state
        state_counts = {}
        for state_val in ('pending', 'collecting', 'collected', 'enriching', 'done', 'error', 'skipped'):
            state_counts[state_val] = Queue.search_count([('state', '=', state_val)])

        res['total_pending'] = state_counts['pending']
        res['total_collecting'] = state_counts['collecting']
        res['total_collected'] = state_counts['collected']
        res['total_enriching'] = state_counts['enriching']
        res['total_done'] = state_counts['done']
        res['total_error'] = state_counts['error']
        res['total_skipped'] = state_counts['skipped']

        total = sum(state_counts.values())
        res['total_all'] = total
        if total > 0:
            res['success_rate'] = round(state_counts['done'] / total * 100, 1)

        # Average times (done items only)
        done_items = Queue.search([('state', '=', 'done')], limit=200, order='date_enriched desc')
        if done_items:
            search_times = [i.processing_time_search for i in done_items if i.processing_time_search]
            ollama_times = [i.processing_time_ollama for i in done_items if i.processing_time_ollama]
            if search_times:
                res['avg_time_search'] = round(sum(search_times) / len(search_times), 1)
            if ollama_times:
                res['avg_time_ollama'] = round(sum(ollama_times) / len(ollama_times), 1)

        # Confidence counts
        res['confidence_high'] = Queue.search_count([
            ('state', '=', 'done'),
            ('product_id.ai_confidence', '=', 'high'),
        ])
        res['confidence_medium'] = Queue.search_count([
            ('state', '=', 'done'),
            ('product_id.ai_confidence', '=', 'medium'),
        ])
        res['confidence_low'] = Queue.search_count([
            ('state', '=', 'done'),
            ('product_id.ai_confidence', '=', 'low'),
        ])

        # Config values
        config = self._get_config()
        if config:
            res['pipeline_running'] = not config.enrichment_paused
            res['current_provider'] = config.provider
            res['current_model'] = config._get_model_name()
            res['ollama_request_timeout'] = config.ollama_request_timeout or 180
            res['ollama_num_ctx'] = config.ollama_num_ctx or 4096
            res['ollama_num_gpu'] = config.ollama_num_gpu if config.ollama_num_gpu is not None else 99
            res['ollama_keep_alive'] = config.ollama_keep_alive or '10m'
            res['cfg_ollama_parallel_workers'] = config.ollama_parallel_workers or 2
            res['cfg_searxng_parallel_workers'] = config.searxng_parallel_workers or 4
            res['cfg_batch_size_collect'] = config.enrichment_batch_size_collect or 20
            res['cfg_batch_size_enrich'] = config.enrichment_batch_size_enrich or 10
            res['cfg_max_tokens'] = config.max_tokens or 4000
            res['cfg_temperature'] = config.temperature if config.temperature is not None else 0.3

        return res

    @api.model
    def _get_config(self):
        try:
            return self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            config = self.env['chatgpt.config'].search([
                ('active', '=', True),
            ], limit=1, order='is_default desc')
            return config or False

    def action_start_pipeline(self):
        config = self._get_config()
        if not config:
            raise UserError(_("No AI configuration found."))
        config.sudo().write({'enrichment_paused': False})
        return self._refresh()

    def action_stop_pipeline(self):
        config = self._get_config()
        if not config:
            raise UserError(_("No AI configuration found."))
        config.sudo().write({'enrichment_paused': True})
        return self._refresh()

    def action_process_now(self):
        self.env['product.enrichment.queue'].action_process_queue_now()
        return self._refresh()

    def action_save_tuning(self):
        config = self._get_config()
        if not config:
            raise UserError(_("No AI configuration found."))
        config.sudo().write({
            'ollama_request_timeout': self.ollama_request_timeout,
            'ollama_num_ctx': self.ollama_num_ctx,
            'ollama_num_gpu': self.ollama_num_gpu,
            'ollama_keep_alive': self.ollama_keep_alive,
            'ollama_parallel_workers': self.cfg_ollama_parallel_workers,
            'searxng_parallel_workers': self.cfg_searxng_parallel_workers,
            'enrichment_batch_size_collect': self.cfg_batch_size_collect,
            'enrichment_batch_size_enrich': self.cfg_batch_size_enrich,
            'max_tokens': self.cfg_max_tokens,
            'temperature': self.cfg_temperature,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Configuration saved'),
                'message': _('Tuning parameters updated.'),
                'type': 'success',
                'sticky': False,
                'next': self._refresh(),
            },
        }

    def action_open_queue(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Enrichment Queue'),
            'res_model': 'product.enrichment.queue',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_open_errors(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Queue Errors'),
            'res_model': 'product.enrichment.queue',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': [('state', '=', 'error')],
        }

    def action_open_config(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Providers'),
            'res_model': 'chatgpt.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def _refresh(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Enrichment Dashboard'),
            'res_model': 'product.enrichment.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
