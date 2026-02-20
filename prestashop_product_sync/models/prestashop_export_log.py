import logging
from odoo import models, fields

_logger = logging.getLogger(__name__)


class PrestaShopExportLog(models.Model):
    _name = 'prestashop.export.log'
    _description = 'PrestaShop Export Log'
    _order = 'create_date desc'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True, ondelete='cascade',
        index=True,
    )
    product_tmpl_id = fields.Many2one(
        'product.template', 'Product', ondelete='set null',
    )
    product_product_id = fields.Many2one(
        'product.product', 'Variant', ondelete='set null',
    )
    operation = fields.Selection([
        ('create', 'Created'),
        ('update', 'Updated'),
        ('price', 'Price Updated'),
        ('stock', 'Stock Updated'),
        ('image', 'Image Exported'),
        ('variant_create', 'Variant Created'),
        ('variant_update', 'Variant Updated'),
        ('category', 'Category Exported'),
        ('error', 'Error'),
    ], string='Operation', required=True)
    ps_product_id = fields.Char('PS Product ID')
    ps_combination_id = fields.Char('PS Combination ID')
    success = fields.Boolean('Success', default=True)
    request_xml = fields.Text('Request XML')
    response_text = fields.Text('Response')
    error_message = fields.Text('Error Message')
    field_changes = fields.Text(
        'Fields Changed (JSON)',
        help="JSON dict of {field: value}",
    )
    user_id = fields.Many2one(
        'res.users', 'User', default=lambda self: self.env.uid,
    )
    duration_ms = fields.Integer('Duration (ms)')
