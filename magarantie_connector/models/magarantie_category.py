# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MaGarantieCategory(models.Model):
    _name = 'magarantie.category'
    _description = 'MaGarantie Category (Rubrique)'
    _order = 'name'
    _rec_name = 'name'

    name = fields.Char(
        string='Title',
        required=True,
        help="Category title from MaGarantie (e.g., Appareil de lavage)",
    )
    rubrique = fields.Char(
        string='Rubrique Code',
        required=True,
        index=True,
        help="Internal rubrique identifier used by the MaGarantie API (e.g., LAVAGE)",
    )
    active = fields.Boolean(default=True)
    product_count = fields.Integer(
        string='Products',
        compute='_compute_product_count',
    )
    warranty_count = fields.Integer(
        string='Warranties',
        compute='_compute_warranty_count',
    )
    warranty_ids = fields.One2many(
        'magarantie.warranty',
        'category_id',
        string='Warranties',
    )

    _sql_constraints = [
        ('rubrique_unique', 'unique(rubrique)', 'Rubrique code must be unique!'),
    ]

    def _compute_product_count(self):
        for rec in self:
            rec.product_count = self.env['product.template'].search_count([
                ('magarantie_category_id', '=', rec.id),
            ])

    @api.depends('warranty_ids')
    def _compute_warranty_count(self):
        for rec in self:
            rec.warranty_count = len(rec.warranty_ids)

    @api.model
    def action_sync_from_api(self):
        """Sync categories from the MaGarantie API."""
        api_client = self.env['res.config.settings']._get_magarantie_api()
        try:
            categories = api_client.get_categories()
            if not isinstance(categories, list):
                categories = [categories] if categories else []

            synced = 0
            for cat in categories:
                rubrique = cat.get('rubrique', '')
                titre = cat.get('titre', '') or cat.get('libelle', '') or rubrique
                if not rubrique:
                    continue

                existing = self.search([('rubrique', '=', rubrique)], limit=1)
                if existing:
                    existing.write({'name': titre})
                else:
                    self.create({'rubrique': rubrique, 'name': titre})
                synced += 1

            _logger.info("MaGarantie: Synced %d categories", synced)
            return synced
        except UserError:
            raise
        except Exception as e:
            _logger.error("MaGarantie category sync failed: %s", str(e))
            raise UserError(_("Category sync failed: %s") % str(e))
