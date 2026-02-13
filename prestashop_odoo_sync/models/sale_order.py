from odoo import models, fields

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    prestashop_instance_id = fields.Many2one('prestashop.instance', string='PrestaShop Instance', readonly=True)
    prestashop_order_id = fields.Char(string='PrestaShop Order ID', readonly=True)
    prestashop_source = fields.Selection([('prestashop', 'PrestaShop')], string='Source', readonly=True)
