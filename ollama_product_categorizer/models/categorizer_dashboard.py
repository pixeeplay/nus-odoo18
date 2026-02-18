# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class ProductCategorizerDashboard(models.TransientModel):
    _name = 'product.categorizer.dashboard'
    _description = 'AI Product Categorizer Dashboard'

    # ------------------------------------------------------------------
    # Statistics fields
    # ------------------------------------------------------------------
    total_products = fields.Integer('Total Products', readonly=True)
    categorized = fields.Integer('Categorized', readonly=True)
    uncategorized = fields.Integer('Uncategorized', readonly=True)
    high_confidence = fields.Integer('High Confidence (>80%)', readonly=True)
    medium_confidence = fields.Integer('Medium (40-80%)', readonly=True)
    low_confidence = fields.Integer('Low Confidence (<40%)', readonly=True)
    total_mappings = fields.Integer('Category Mappings', readonly=True)
    ai_provider = fields.Char('AI Provider', readonly=True)
    ai_model = fields.Char('AI Model', readonly=True)

    # ------------------------------------------------------------------
    # Default values — compute stats
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Product = self.env['product.template'].sudo()

        total = Product.search_count([])
        categorized = Product.search_count([
            ('ai_category_suggestion', '!=', False),
            ('ai_category_suggestion', '!=', ''),
        ])
        uncategorized = total - categorized

        high = Product.search_count([
            ('ai_category_suggestion', '!=', False),
            ('ai_category_suggestion', '!=', ''),
            ('ai_category_confidence', '>', 80),
        ])
        medium = Product.search_count([
            ('ai_category_suggestion', '!=', False),
            ('ai_category_suggestion', '!=', ''),
            ('ai_category_confidence', '>=', 40),
            ('ai_category_confidence', '<=', 80),
        ])
        low = Product.search_count([
            ('ai_category_suggestion', '!=', False),
            ('ai_category_suggestion', '!=', ''),
            ('ai_category_confidence', '<', 40),
        ])

        res.update({
            'total_products': total,
            'categorized': categorized,
            'uncategorized': uncategorized,
            'high_confidence': high,
            'medium_confidence': medium,
            'low_confidence': low,
            'total_mappings': self.env['product.ai.category.mapping'].search_count([]),
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
    def action_categorize_all(self):
        """Batch categorize all products that lack an AI category suggestion."""
        products = self.env['product.template'].search([
            '|',
            ('ai_category_suggestion', '=', False),
            ('ai_category_suggestion', '=', ''),
        ])
        if not products:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nothing to Categorize'),
                    'message': _('All products already have an AI category.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for product in products:
            try:
                product.action_categorize_product()
                if product.ai_category_suggestion:
                    success += 1
                    # Auto-apply mapping if configured
                    mapping = self.env['product.ai.category.mapping'].search([
                        ('name', '=', product.ai_category_suggestion),
                        ('auto_apply', '=', True),
                    ], limit=1)
                    if mapping and mapping.odoo_category_id:
                        product.categ_id = mapping.odoo_category_id.id
                        mapping.sudo().write({
                            'match_count': mapping.match_count + 1,
                        })
                else:
                    errors += 1
            except Exception as e:
                _logger.error("Batch categorization error for product %s: %s", product.id, e)
                errors += 1

        msg = _('%d products categorized successfully.') % success
        if errors:
            msg += _(' %d errors.') % errors

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Categorization Complete'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    def action_open_uncategorized(self):
        """Open list of products without AI categorization."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Uncategorized Products'),
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [
                '|',
                ('ai_category_suggestion', '=', False),
                ('ai_category_suggestion', '=', ''),
            ],
            'target': 'current',
        }

    def action_open_mappings(self):
        """Open category mapping list."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Category Mappings'),
            'res_model': 'product.ai.category.mapping',
            'view_mode': 'list,form',
            'target': 'current',
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
            'name': _('AI Product Categorizer'),
            'res_model': 'product.categorizer.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
