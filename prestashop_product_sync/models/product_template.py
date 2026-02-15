from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    prestashop_id = fields.Char(
        'PrestaShop ID', readonly=True, copy=False, index=True,
    )
    prestashop_instance_id = fields.Many2one(
        'prestashop.instance', 'PrestaShop Instance',
        readonly=True, ondelete='set null', copy=False,
    )
    prestashop_url = fields.Char('PrestaShop URL', readonly=True)
    prestashop_last_sync = fields.Datetime('Last Sync', readonly=True)
    prestashop_description_html = fields.Html(
        'PS Full Description', readonly=True, sanitize=False,
    )
    prestashop_description_short_html = fields.Html(
        'PS Short Description', readonly=True, sanitize=False,
    )
    prestashop_meta_title = fields.Char('PS Meta Title', readonly=True)
    prestashop_meta_description = fields.Text('PS Meta Description', readonly=True)
    prestashop_manufacturer = fields.Char('PS Manufacturer', readonly=True)
    prestashop_ean13 = fields.Char('PS EAN13', readonly=True)


class ProductCategory(models.Model):
    _inherit = 'product.category'

    prestashop_id = fields.Char('PrestaShop ID', index=True)
    prestashop_instance_id = fields.Many2one(
        'prestashop.instance', 'PrestaShop Instance', ondelete='set null',
    )
