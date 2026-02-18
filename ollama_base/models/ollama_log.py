# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class OllamaLog(models.Model):
    _name = 'ollama.log'
    _description = 'AI Call Log'
    _order = 'create_date desc'
    _rec_name = 'config_id'

    config_id = fields.Many2one('ollama.config', string='Config', ondelete='set null')
    provider = fields.Char(string='Provider')
    model_name = fields.Char(string='Model')
    prompt_preview = fields.Text(string='Prompt (preview)')
    response_preview = fields.Text(string='Response (preview)')
    res_model = fields.Char(string='Source Model')
    res_id = fields.Integer(string='Source Record ID')
    status = fields.Selection([
        ('success', 'Success'),
        ('error', 'Error'),
    ], string='Status', default='success')

    @api.autovacuum
    def _gc_old_logs(self):
        """Remove AI logs older than 30 days (called by autovacuum)."""
        limit_date = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        old = self.search([('create_date', '<', limit_date)])
        count = len(old)
        if count:
            old.unlink()
            _logger.info("Cleaned up %d old AI logs.", count)
