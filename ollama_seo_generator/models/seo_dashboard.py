# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SeoGeneratorDashboard(models.TransientModel):
    _name = 'seo.generator.dashboard'
    _description = 'SEO Generator Dashboard'

    # ------------------------------------------------------------------
    # Statistic Fields
    # ------------------------------------------------------------------
    total_products = fields.Integer(
        string='Total Products',
        readonly=True,
    )
    with_seo = fields.Integer(
        string='With SEO',
        readonly=True,
    )
    without_seo = fields.Integer(
        string='Without SEO',
        readonly=True,
    )
    avg_score = fields.Integer(
        string='Average Score',
        readonly=True,
    )
    excellent_score = fields.Integer(
        string='Excellent (>80)',
        readonly=True,
    )
    weak_score = fields.Integer(
        string='Weak (<40)',
        readonly=True,
    )
    applied_count = fields.Integer(
        string='Applied to Website',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Default Get — Compute Stats
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Product = self.env['product.template']

        all_products = Product.search([])
        total = len(all_products)
        with_seo = Product.search_count([('ai_seo_title', '!=', False)])
        without_seo = total - with_seo

        # Average score
        products_with_score = Product.search([('ai_seo_score', '>', 0)])
        avg = 0
        if products_with_score:
            scores = products_with_score.mapped('ai_seo_score')
            avg = int(sum(scores) / len(scores)) if scores else 0

        # Excellent (>80) and Weak (<40)
        excellent = Product.search_count([('ai_seo_score', '>', 80)])
        weak = Product.search_count([
            ('ai_seo_score', '>', 0),
            ('ai_seo_score', '<', 40),
        ])

        # Applied: products where website_meta_title is set AND ai_seo_title is set
        applied = Product.search_count([
            ('website_meta_title', '!=', False),
            ('ai_seo_title', '!=', False),
        ])

        res.update({
            'total_products': total,
            'with_seo': with_seo,
            'without_seo': without_seo,
            'avg_score': avg,
            'excellent_score': excellent,
            'weak_score': weak,
            'applied_count': applied,
        })
        return res

    # ------------------------------------------------------------------
    # Bulk Actions
    # ------------------------------------------------------------------
    def action_generate_all(self):
        """Generate SEO for all products that do not yet have AI SEO data."""
        products = self.env['product.template'].search([
            ('ai_seo_title', '=', False),
            ('name', '!=', False),
        ])
        if not products:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nothing to Generate'),
                    'message': _('All products already have AI SEO data.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for product in products:
            try:
                product.action_generate_seo()
                success += 1
            except Exception as e:
                errors += 1
                _logger.warning(
                    'SEO generation failed for product %s (ID %s): %s',
                    product.name, product.id, e,
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bulk SEO Generation Complete'),
                'message': _('%d products generated, %d errors.') % (
                    success, errors),
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    def action_audit_all(self):
        """Run SEO audit on all products that have a name."""
        products = self.env['product.template'].search([
            ('name', '!=', False),
        ])
        if not products:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Products'),
                    'message': _('No products found to audit.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        success = 0
        errors = 0
        for product in products:
            try:
                product.action_audit_seo()
                success += 1
            except Exception as e:
                errors += 1
                _logger.warning(
                    'SEO audit failed for product %s (ID %s): %s',
                    product.name, product.id, e,
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Catalog Audit Complete'),
                'message': _('%d products audited, %d errors.') % (
                    success, errors),
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    def action_apply_all(self):
        """Apply AI SEO data to website meta fields for all products."""
        products = self.env['product.template'].search([
            ('ai_seo_title', '!=', False),
        ])
        if not products:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nothing to Apply'),
                    'message': _('No products have AI SEO data to apply.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        count = 0
        for product in products:
            vals = {}
            if product.ai_seo_title:
                vals['website_meta_title'] = product.ai_seo_title
            if product.ai_seo_description:
                vals['website_meta_description'] = product.ai_seo_description
            if vals:
                product.write(vals)
                count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bulk Apply Complete'),
                'message': _('%d products updated with AI SEO metadata.') % count,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_open_without_seo(self):
        """Open a list of products that have no AI SEO data."""
        return {
            'name': _('Products Without SEO'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [('ai_seo_title', '=', False)],
            'context': {'search_default_filter_published': 0},
        }

    def action_open_config(self):
        """Open the Ollama AI configuration."""
        return {
            'name': _('AI Configuration'),
            'type': 'ir.actions.act_window',
            'res_model': 'ollama.config',
            'view_mode': 'form',
            'target': 'current',
        }

    def action_refresh(self):
        """Refresh the dashboard by reloading the form view."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'seo.generator.dashboard',
            'view_mode': 'form',
            'target': 'main',
        }
