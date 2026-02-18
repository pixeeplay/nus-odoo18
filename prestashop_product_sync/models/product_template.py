from odoo import models, fields, _
from odoo.exceptions import UserError


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
    prestashop_active = fields.Boolean(
        'Active in PrestaShop', readonly=True, default=True,
        help="Whether this product is currently active in PrestaShop.",
    )
    prestashop_ecotax = fields.Float(
        'PS Eco-Tax', readonly=True, digits=(12, 4),
        help="Eco-tax amount from PrestaShop.",
    )
    prestashop_tax_rules_group_id = fields.Char(
        'PS Tax Group ID', readonly=True,
    )
    prestashop_tax_rate = fields.Float(
        'PS Tax Rate (%)', readonly=True, digits=(5, 2),
    )
    prestashop_tax_id = fields.Many2one(
        'account.tax', 'Mapped Tax',
        help="Odoo tax mapped from PrestaShop tax rules group.",
    )

    def action_open_reimport_wizard(self):
        ps_products = self.filtered(
            lambda p: p.prestashop_id and p.prestashop_instance_id)
        if not ps_products:
            raise UserError(_("No PrestaShop products selected."))
        instance = ps_products[0].prestashop_instance_id
        wizard = self.env['prestashop.reimport.wizard'].create({
            'instance_id': instance.id,
            'product_ids': [(6, 0, ps_products.ids)],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Re-import from PrestaShop'),
            'res_model': 'prestashop.reimport.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ProductCategory(models.Model):
    _inherit = 'product.category'

    prestashop_id = fields.Char('PrestaShop ID', index=True)
    prestashop_instance_id = fields.Many2one(
        'prestashop.instance', 'PrestaShop Instance', ondelete='set null',
    )
