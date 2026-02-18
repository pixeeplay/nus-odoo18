# -*- coding: utf-8 -*-
import logging
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CatalogTranslationWizard(models.TransientModel):
    _name = 'catalog.translation.wizard'
    _inherit = ['ollama.mixin']
    _description = 'Batch Product Translation Wizard'

    product_ids = fields.Many2many(
        'product.template',
        string='Products to Translate',
        help='Select products to translate. Leave empty to translate all products.',
    )
    source_lang = fields.Selection(
        selection=[
            ('fr', 'French'),
            ('en', 'English'),
            ('de', 'German'),
            ('es', 'Spanish'),
        ],
        string='Source Language',
        default='fr',
        required=True,
    )
    target_lang_en = fields.Boolean(string='English', default=True)
    target_lang_de = fields.Boolean(string='German', default=True)
    target_lang_es = fields.Boolean(string='Spanish', default=True)
    target_lang_it = fields.Boolean(string='Italian', default=False)

    include_name = fields.Boolean(string='Translate Product Name', default=True)
    include_description = fields.Boolean(string='Translate Description', default=True)

    state = fields.Selection(
        selection=[
            ('draft', 'Configuration'),
            ('running', 'Running'),
            ('done', 'Completed'),
        ],
        string='Status',
        default='draft',
        readonly=True,
    )
    log = fields.Html(string='Translation Log', readonly=True)

    product_count = fields.Integer(
        string='Number of Products',
        compute='_compute_product_count',
    )

    @api.depends('product_ids')
    def _compute_product_count(self):
        for rec in self:
            rec.product_count = len(rec.product_ids)

    # ------------------------------------------------------------------
    # Main translation action
    # ------------------------------------------------------------------
    def action_translate(self):
        """Run batch translation on the selected products."""
        self.ensure_one()

        # Determine target languages
        target_langs = []
        if self.target_lang_en:
            target_langs.append('en')
        if self.target_lang_de:
            target_langs.append('de')
        if self.target_lang_es:
            target_langs.append('es')
        if self.target_lang_it:
            target_langs.append('it')

        if not target_langs:
            raise UserError(_('Please select at least one target language.'))

        if not self.include_name and not self.include_description:
            raise UserError(_('Please select at least one field to translate (name or description).'))

        # Determine products
        products = self.product_ids
        if not products:
            products = self.env['product.template'].search([
                '|',
                ('ai_translation_status', '=', 'none'),
                ('ai_translation_status', '=', False),
            ])

        if not products:
            raise UserError(_('No products found to translate.'))

        self.write({'state': 'running'})

        success_count = 0
        error_count = 0
        log_lines = []
        start_time = datetime.now()

        log_lines.append(
            f'<div style="padding:8px; background:#E8F5E9; border-radius:4px; margin-bottom:8px;">'
            f'<strong>Batch Translation Started</strong><br/>'
            f'Products: {len(products)} | Languages: {", ".join(target_langs)}'
            f'</div>'
        )

        for idx, product in enumerate(products, 1):
            try:
                product.action_translate_product(target_langs=target_langs)
                success_count += 1
                log_lines.append(
                    f'<div style="padding:4px 8px; color:#2E7D32;">'
                    f'[{idx}/{len(products)}] {product.name} - OK'
                    f'</div>'
                )
            except Exception as e:
                error_count += 1
                error_msg = str(e)[:200]
                log_lines.append(
                    f'<div style="padding:4px 8px; color:#C62828;">'
                    f'[{idx}/{len(products)}] {product.name} - ERROR: {error_msg}'
                    f'</div>'
                )
                _logger.error(
                    "Translation error for product %s (ID %s): %s",
                    product.name, product.id, e,
                )

        elapsed = (datetime.now() - start_time).total_seconds()
        log_lines.append(
            f'<div style="padding:8px; background:#E8F5E9; border-radius:4px; margin-top:8px;">'
            f'<strong>Batch Translation Complete</strong><br/>'
            f'Success: {success_count} | Errors: {error_count} | '
            f'Time: {elapsed:.1f}s'
            f'</div>'
        )

        self.write({
            'state': 'done',
            'log': '\n'.join(log_lines),
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
