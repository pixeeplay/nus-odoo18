from odoo import models, fields, api, _
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

    # --- Export Configuration (per product) ---
    ps_export_enabled = fields.Boolean(
        'Export to PrestaShop', default=False,
        help="Flag this product for export/sync to PrestaShop.",
    )
    ps_stock_location_id = fields.Many2one(
        'stock.location', 'PS Stock Location',
        help="Stock location used to compute quantity for PrestaShop. "
             "If empty, the instance warehouse's stock location is used.",
    )
    ps_export_price_type = fields.Selection([
        ('list_price', 'Sales Price'),
        ('standard_price', 'Cost Price'),
        ('pricelist', 'Pricelist'),
    ], default='list_price', string='PS Price Source',
        help="Which Odoo price to push to PrestaShop.",
    )
    ps_export_pricelist_id = fields.Many2one(
        'product.pricelist', 'PS Pricelist',
        help="Pricelist to use when Price Source is 'Pricelist'.",
    )
    ps_last_export = fields.Datetime('Last Export to PS', readonly=True)
    ps_export_state = fields.Selection([
        ('not_exported', 'Not Exported'),
        ('exported', 'Exported'),
        ('modified', 'Modified Locally'),
        ('error', 'Export Error'),
    ], default='not_exported', string='Export Status', readonly=True)
    ps_export_error = fields.Text('Last Export Error', readonly=True)
    ps_combination_ids_json = fields.Text(
        'PS Combination IDs (JSON)', readonly=True,
        help="JSON mapping of product.product IDs to PS combination IDs.",
    )

    # Fields that trigger 'modified' state when changed after export
    _PS_EXPORT_TRIGGER_FIELDS = {
        'name', 'list_price', 'standard_price', 'default_code',
        'barcode', 'weight', 'description', 'description_sale',
        'categ_id', 'image_1920',
    }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        return records

    def write(self, vals):
        res = super().write(vals)
        if self._PS_EXPORT_TRIGGER_FIELDS & set(vals.keys()):
            exported = self.filtered(
                lambda p: p.ps_export_state == 'exported'
            )
            if exported:
                exported.with_context(skip_export_tracking=True).write({
                    'ps_export_state': 'modified',
                })
        return res

    def action_export_to_prestashop(self):
        """Manual export button from product form or list multi-select."""
        products = self.filtered(lambda p: p.ps_export_enabled)
        if not products:
            products = self
        # Find instance: prefer one already linked to a selected product
        instance = False
        for p in products:
            if p.prestashop_instance_id:
                instance = p.prestashop_instance_id
                break
        if not instance:
            instance = self.env['prestashop.instance'].search([
                ('active', '=', True),
            ], limit=1)
        if not instance:
            raise UserError(_("No active PrestaShop instance found."))
        wizard = self.env['prestashop.export.wizard'].create({
            'instance_id': instance.id,
            'product_ids': [(6, 0, products.ids)],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export to PrestaShop'),
            'res_model': 'prestashop.export.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_mark_for_export(self):
        """Mark selected products for PS export."""
        self.write({'ps_export_enabled': True})

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
