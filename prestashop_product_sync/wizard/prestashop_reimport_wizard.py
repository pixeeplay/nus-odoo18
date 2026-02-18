import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopReimportWizard(models.TransientModel):
    _name = 'prestashop.reimport.wizard'
    _description = 'PrestaShop Selective Re-import Wizard'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True,
    )
    product_ids = fields.Many2many(
        'product.template', string='Products to Re-import',
    )
    product_count = fields.Integer(
        'Product Count', compute='_compute_product_count',
    )

    # Field selection checkboxes
    sync_name = fields.Boolean('Name', default=True)
    sync_price = fields.Boolean('Price', default=True)
    sync_description = fields.Boolean('HTML Descriptions', default=False)
    sync_images = fields.Boolean('Images', default=False)
    sync_features = fields.Boolean('Characteristics', default=False)
    sync_stock = fields.Boolean('Stock', default=True)
    sync_ecotax = fields.Boolean('Eco-Tax', default=True)
    sync_tax = fields.Boolean('Tax', default=True)
    sync_category = fields.Boolean('Category', default=False)
    sync_meta = fields.Boolean('SEO (meta)', default=False)
    sync_manufacturer = fields.Boolean('Manufacturer', default=False)
    sync_weight = fields.Boolean('Weight', default=False)
    sync_wholesale = fields.Boolean("Wholesale Price", default=False)
    sync_barcode = fields.Boolean('Barcode / EAN13', default=False)
    select_all = fields.Boolean('Select All')

    state = fields.Selection([
        ('draft', 'Ready'),
        ('running', 'Running'),
        ('done', 'Done'),
    ], default='draft')
    log = fields.Html('Log', readonly=True)

    @api.depends('product_ids')
    def _compute_product_count(self):
        for rec in self:
            rec.product_count = len(rec.product_ids)

    @api.onchange('select_all')
    def _onchange_select_all(self):
        val = self.select_all
        for fname in (
            'sync_name', 'sync_price', 'sync_description', 'sync_images',
            'sync_features', 'sync_stock', 'sync_ecotax', 'sync_tax',
            'sync_category', 'sync_meta', 'sync_manufacturer',
            'sync_weight', 'sync_wholesale', 'sync_barcode',
        ):
            setattr(self, fname, val)

    def action_reimport(self):
        """Re-import selected fields for all selected products."""
        self.ensure_one()
        if not self.product_ids:
            raise UserError(_("No products selected."))

        instance = self.instance_id
        log_lines = []
        success = errors = 0

        self.write({'state': 'running'})

        for product in self.product_ids:
            ps_id = product.prestashop_id
            if not ps_id:
                log_lines.append(
                    '<span class="text-warning">%s — no PS ID, skipped</span>'
                    % product.name
                )
                continue

            try:
                ps_product = instance._fetch_single_product_full(ps_id)
                if not ps_product:
                    log_lines.append(
                        '<span class="text-danger">PS-%s — empty API response</span>'
                        % ps_id
                    )
                    errors += 1
                    continue

                vals = self._build_reimport_vals(instance, product, ps_product)
                if vals:
                    product.write(vals)

                # Extra sync actions
                associations = ps_product.get('associations', {}) or {}
                if self.sync_images and instance.sync_product_images:
                    img_list = instance._normalize_association_list(associations, 'images', 'image')
                    instance._sync_product_images_to_odoo(product, ps_id, img_list)

                if self.sync_features and instance.sync_product_features:
                    feat_list = instance._normalize_association_list(associations, 'product_features', 'product_feature')
                    instance._sync_product_features_to_odoo(product, feat_list)

                if self.sync_stock and instance.sync_product_stock:
                    instance._sync_product_stock(product, ps_id)

                success += 1
                log_lines.append(
                    '<span class="text-success">%s — OK (%d fields updated)</span>'
                    % (product.name, len(vals))
                )
            except Exception as exc:
                errors += 1
                log_lines.append(
                    '<span class="text-danger">%s — ERROR: %s</span>'
                    % (product.name, exc)
                )
                _logger.error("Reimport failed for %s: %s", product.name, exc)

        summary = '<strong>Done: %d OK, %d errors</strong><br/>' % (success, errors)
        self.write({
            'state': 'done',
            'log': summary + '<br/>'.join(log_lines),
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Re-import Results'),
            'res_model': 'prestashop.reimport.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _build_reimport_vals(self, instance, product, ps_product):
        """Build vals dict based on selected checkboxes."""
        vals = {'prestashop_last_sync': fields.Datetime.now()}
        ps_id = str(ps_product.get('id', ''))

        if self.sync_name:
            name = instance._get_ps_text(ps_product.get('name', ''))
            if name:
                vals['name'] = name

        if self.sync_price:
            try:
                vals['list_price'] = float(ps_product.get('price', 0) or 0)
            except (ValueError, TypeError):
                pass

        if self.sync_wholesale:
            try:
                vals['standard_price'] = float(
                    ps_product.get('wholesale_price', 0) or 0
                )
            except (ValueError, TypeError):
                pass

        if self.sync_weight:
            try:
                vals['weight'] = float(ps_product.get('weight', 0) or 0)
            except (ValueError, TypeError):
                pass

        if self.sync_description:
            desc = instance._get_ps_text(ps_product.get('description', ''))
            desc_short = instance._get_ps_text(
                ps_product.get('description_short', '')
            )
            vals['description'] = desc or False
            vals['description_sale'] = desc_short or False
            vals['prestashop_description_html'] = desc or False
            vals['prestashop_description_short_html'] = desc_short or False

        if self.sync_ecotax:
            try:
                vals['prestashop_ecotax'] = float(
                    ps_product.get('ecotax', 0) or 0
                )
            except (ValueError, TypeError):
                pass

        if self.sync_tax:
            tax_group_id = str(ps_product.get('id_tax_rules_group', '0'))
            if tax_group_id and tax_group_id != '0':
                vals['prestashop_tax_rules_group_id'] = tax_group_id
                try:
                    rate, _name = instance._resolve_tax_rate(tax_group_id)
                    vals['prestashop_tax_rate'] = rate
                    odoo_tax = instance._find_odoo_tax(rate)
                    if odoo_tax:
                        vals['prestashop_tax_id'] = odoo_tax.id
                except Exception:
                    pass

        if self.sync_category:
            cat_id = str(ps_product.get('id_category_default', '0'))
            if cat_id and cat_id not in ('0', '1', '2'):
                categ = instance._get_or_create_category(cat_id)
                if categ:
                    vals['categ_id'] = categ.id

        if self.sync_meta:
            vals['prestashop_meta_title'] = instance._get_ps_text(
                ps_product.get('meta_title', '')
            ) or False
            vals['prestashop_meta_description'] = instance._get_ps_text(
                ps_product.get('meta_description', '')
            ) or False

        if self.sync_manufacturer:
            mfr_id = ps_product.get('id_manufacturer', '0')
            vals['prestashop_manufacturer'] = instance._get_manufacturer_name(
                mfr_id
            ) or False

        if self.sync_barcode:
            ean = str(ps_product.get('ean13', '') or '')
            vals['prestashop_ean13'] = ean or False
            if ean and len(ean) in (8, 12, 13, 14):
                dup = instance.env['product.template'].search([
                    ('barcode', '=', ean),
                    ('id', '!=', product.id),
                ], limit=1)
                if not dup:
                    vals['barcode'] = ean

        return vals
