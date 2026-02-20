from odoo import fields, models


class NovaProductLabel(models.Model):
    _name = 'nova.product.label'
    _description = 'Product Label'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    style = fields.Selection([
        ('badge', 'Badge'),
        ('ribbon', 'Ribbon'),
        ('pill', 'Pill'),
        ('corner', 'Corner'),
    ], default='badge', required=True)
    background_color = fields.Char(default='#e63946')
    text_color = fields.Char(default='#ffffff')
    active = fields.Boolean(default=True)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    nova_label_id = fields.Many2one(
        'nova.product.label', string='Product Label',
        help='Display a visual label on the product card (e.g., New, Sale, Limited)',
    )
