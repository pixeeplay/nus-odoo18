# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class CatalogTranslatorDashboard(models.TransientModel):
    _name = 'catalog.translator.dashboard'
    _description = 'AI Catalog Translator Dashboard'

    total_products = fields.Integer(string='Total Products', readonly=True)
    translated = fields.Integer(string='Translated Products', readonly=True)
    untranslated = fields.Integer(string='Untranslated Products', readonly=True)
    partial = fields.Integer(string='Partially Translated', readonly=True)
    complete = fields.Integer(string='Fully Translated', readonly=True)
    by_en = fields.Integer(string='With English', readonly=True)
    by_de = fields.Integer(string='With German', readonly=True)
    by_es = fields.Integer(string='With Spanish', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Product = self.env['product.template']

        total = Product.search_count([])
        complete = Product.search_count([('ai_translation_status', '=', 'complete')])
        partial = Product.search_count([('ai_translation_status', '=', 'partial')])
        none_count = Product.search_count([
            '|',
            ('ai_translation_status', '=', 'none'),
            ('ai_translation_status', '=', False),
        ])
        translated = complete + partial
        by_en = Product.search_count([('ai_translation_en', '!=', False)])
        by_de = Product.search_count([('ai_translation_de', '!=', False)])
        by_es = Product.search_count([('ai_translation_es', '!=', False)])

        res.update({
            'total_products': total,
            'translated': translated,
            'untranslated': none_count,
            'partial': partial,
            'complete': complete,
            'by_en': by_en,
            'by_de': by_de,
            'by_es': by_es,
        })
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_translate_all(self):
        """Open the batch translation wizard pre-filled with all untranslated products."""
        Product = self.env['product.template']
        untranslated = Product.search([
            '|',
            ('ai_translation_status', '=', 'none'),
            ('ai_translation_status', '=', False),
        ])
        return {
            'name': _('Batch Translate Products'),
            'type': 'ir.actions.act_window',
            'res_model': 'catalog.translation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_ids': untranslated.ids,
            },
        }

    def action_open_untranslated(self):
        """Open a list of untranslated products."""
        return {
            'name': _('Untranslated Products'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [
                '|',
                ('ai_translation_status', '=', 'none'),
                ('ai_translation_status', '=', False),
            ],
            'target': 'current',
        }

    def action_open_config(self):
        """Open the AI configuration."""
        return {
            'name': _('AI Configuration'),
            'type': 'ir.actions.act_window',
            'res_model': 'ollama.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def _refresh(self):
        """Refresh the dashboard by reloading the form."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'catalog.translator.dashboard',
            'view_mode': 'form',
            'target': 'inline',
            'flags': {'mode': 'readonly'},
        }
