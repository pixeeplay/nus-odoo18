from odoo import models, fields, api


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    prestashop_instance_id = fields.Many2one('prestashop.instance', string='PrestaShop Instance', readonly=True)
    prestashop_order_id = fields.Char(string='PrestaShop Order ID', readonly=True)
    prestashop_source = fields.Selection([('prestashop', 'PrestaShop')], string='Source', readonly=True)
    prestashop_total_ecotax = fields.Float(string='Total Eco-Tax', compute='_compute_total_ecotax', store=True)

    @api.depends('order_line.prestashop_ecotax')
    def _compute_total_ecotax(self):
        for order in self:
            order.prestashop_total_ecotax = sum(
                line.prestashop_ecotax * line.product_uom_qty
                for line in order.order_line
            )


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    prestashop_ecotax = fields.Float(string='Eco-Tax', readonly=True, digits=(12, 4))
