# -*- coding: utf-8 -*-
from odoo import models, fields


class ProductAICategoryMapping(models.Model):
    _name = 'product.ai.category.mapping'
    _description = 'AI Category to Odoo Category Mapping'
    _order = 'match_count desc, name'

    name = fields.Char(
        string='AI Category',
        required=True,
        help='The AI category path or name to match (e.g. "Electronics > Phones").',
    )
    odoo_category_id = fields.Many2one(
        'product.category',
        string='Odoo Category',
        required=True,
        ondelete='cascade',
        help='The Odoo product category to apply when this AI category is matched.',
    )
    auto_apply = fields.Boolean(
        string='Auto Apply',
        default=False,
        help='Automatically apply this mapping when the AI suggests this category.',
    )
    match_count = fields.Integer(
        string='Times Matched',
        readonly=True,
        default=0,
        help='Number of times this mapping has been applied to a product.',
    )

    _sql_constraints = [
        ('name_unique', 'unique(name)',
         'A mapping for this AI category already exists!'),
    ]
