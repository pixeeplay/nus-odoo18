from odoo import fields, models


class ProductSupplierInfo(models.Model):
    _inherit = 'product.supplierinfo'

    pm_supplier_stock = fields.Integer(string='PM Stock', default=0)
    pm_is_best = fields.Boolean(string='Best Supplier')
