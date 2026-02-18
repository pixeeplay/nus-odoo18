import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopProductPreview(models.Model):
    _name = 'prestashop.product.preview'
    _description = 'PrestaShop Product Preview'
    _order = 'state_sequence, prestashop_id asc'
    _rec_name = 'name'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True,
        ondelete='cascade', index=True,
    )
    prestashop_id = fields.Char('PS ID', required=True, index=True)
    name = fields.Char('Product Name')
    reference = fields.Char('Reference / SKU')
    price = fields.Float('Price (HT)')
    ean13 = fields.Char('EAN13')
    active_in_ps = fields.Boolean('Active in PS', default=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('importing', 'Importing...'),
        ('imported', 'Imported'),
        ('updated', 'Updated'),
        ('error', 'Error'),
        ('skipped', 'Skipped'),
    ], default='pending', string='Status', index=True)
    state_sequence = fields.Integer(
        compute='_compute_state_sequence', store=True,
        help="Used for ordering: importing first, then pending, then errors, then done.",
    )
    imported_product_id = fields.Many2one(
        'product.template', 'Odoo Product', readonly=True,
    )
    error_message = fields.Text('Error Details')
    import_date = fields.Datetime('Import Date', readonly=True)
    raw_data = fields.Text('Raw API Data', readonly=True)
    # Extended preview fields (populated during import or debug fetch)
    description_preview = fields.Text('Description (preview)', readonly=True)
    description_short_preview = fields.Text('Short Description (preview)', readonly=True)
    category_name = fields.Char('Category', readonly=True)
    manufacturer_name = fields.Char('Manufacturer', readonly=True)
    image_count = fields.Integer('Image Count', readonly=True)
    feature_count = fields.Integer('Feature Count', readonly=True)
    weight = fields.Float('Weight', readonly=True)
    wholesale_price = fields.Float('Wholesale Price', readonly=True)
    ecotax = fields.Float('Eco-Tax', readonly=True, digits=(12, 4))
    tax_rate = fields.Float('Tax Rate (%)', readonly=True, digits=(5, 2))
    tax_group_name = fields.Char('Tax Group', readonly=True)
    image_preview = fields.Binary('Thumbnail', attachment=True, readonly=True)

    # Computed: price comparison
    odoo_price = fields.Float(
        'Odoo Price', compute='_compute_odoo_price', store=True,
    )
    price_diff = fields.Float(
        'Price Diff', compute='_compute_odoo_price', store=True,
    )
    price_match = fields.Boolean(
        'Price Match', compute='_compute_odoo_price', store=True,
    )

    _sql_constraints = [
        ('unique_ps_product_instance',
         'unique(instance_id, prestashop_id)',
         'This product is already in the preview for this instance.'),
    ]

    @api.depends('state')
    def _compute_state_sequence(self):
        order = {
            'importing': 0,
            'pending': 1,
            'error': 2,
            'imported': 3,
            'updated': 4,
            'skipped': 5,
        }
        for rec in self:
            rec.state_sequence = order.get(rec.state, 9)

    @api.depends('price', 'imported_product_id', 'imported_product_id.list_price')
    def _compute_odoo_price(self):
        for rec in self:
            if rec.imported_product_id:
                rec.odoo_price = rec.imported_product_id.list_price
                rec.price_diff = round(rec.price - rec.imported_product_id.list_price, 4)
                rec.price_match = abs(rec.price_diff) < 0.01
            else:
                rec.odoo_price = 0.0
                rec.price_diff = 0.0
                rec.price_match = True

    def _update_preview_from_ps_data(self, ps_product):
        """Update preview extra fields from full PS product data."""
        instance = self.instance_id
        vals = {}

        # Name (use parsed name from PS)
        parsed_name = instance._get_ps_text(ps_product.get('name', ''))
        if parsed_name:
            vals['name'] = parsed_name

        # Price
        try:
            vals['price'] = float(ps_product.get('price', 0) or 0)
        except (ValueError, TypeError):
            pass

        # Reference
        ref = ps_product.get('reference', '')
        if ref:
            vals['reference'] = str(ref)

        # EAN13
        ean = ps_product.get('ean13', '')
        if ean:
            vals['ean13'] = str(ean)

        # Description preview (plain text, truncated)
        desc = instance._get_ps_text(ps_product.get('description', ''))
        if desc:
            vals['description_preview'] = desc[:500]

        desc_short = instance._get_ps_text(ps_product.get('description_short', ''))
        if desc_short:
            vals['description_short_preview'] = desc_short[:300]

        # Weight / wholesale
        try:
            vals['weight'] = float(ps_product.get('weight', 0) or 0)
        except (ValueError, TypeError):
            pass
        try:
            vals['wholesale_price'] = float(ps_product.get('wholesale_price', 0) or 0)
        except (ValueError, TypeError):
            pass

        # Associations-based counts
        associations = ps_product.get('associations', {}) or {}
        images = associations.get('images', {}) or {}
        image_list = images.get('image', [])
        if isinstance(image_list, dict):
            image_list = [image_list]
        vals['image_count'] = len(image_list) if isinstance(image_list, list) else 0

        features = associations.get('product_features', {}) or {}
        feat_list = features.get('product_feature', [])
        if isinstance(feat_list, dict):
            feat_list = [feat_list]
        vals['feature_count'] = len(feat_list) if isinstance(feat_list, list) else 0

        # Category name (from API)
        cat_id = ps_product.get('id_category_default', '0')
        if cat_id and str(cat_id) not in ('0', '1', '2'):
            try:
                cat_data = instance._api_get('categories', str(cat_id))
                cat_name = instance._get_ps_text(
                    cat_data.get('category', {}).get('name', '')
                )
                vals['category_name'] = cat_name or f'Category {cat_id}'
            except Exception:
                vals['category_name'] = f'Category {cat_id}'

        # Manufacturer name
        mfr_id = ps_product.get('id_manufacturer', '0')
        if mfr_id and str(mfr_id) != '0':
            vals['manufacturer_name'] = instance._get_manufacturer_name(mfr_id)

        # Eco-tax
        try:
            vals['ecotax'] = float(ps_product.get('ecotax', 0) or 0)
        except (ValueError, TypeError):
            pass

        # Tax rate (resolve from tax_rules_group)
        tax_group_id = ps_product.get('id_tax_rules_group', '0')
        if tax_group_id and str(tax_group_id) != '0':
            try:
                rate, group_name = instance._resolve_tax_rate(str(tax_group_id))
                vals['tax_rate'] = rate
                vals['tax_group_name'] = group_name or f'Group {tax_group_id}'
            except Exception:
                vals['tax_group_name'] = f'Group {tax_group_id}'

        # First image thumbnail
        if image_list and isinstance(image_list, list) and image_list:
            first_img = image_list[0]
            img_id = str(first_img.get('id', '')) if isinstance(first_img, dict) else str(first_img)
            if img_id:
                try:
                    b64 = instance._download_image(str(ps_product.get('id', '')), img_id)
                    if b64:
                        vals['image_preview'] = b64
                except Exception:
                    pass

        if vals:
            self.write(vals)

    def action_debug_fetch(self):
        """Fetch full API response, store raw_data and update preview fields."""
        self.ensure_one()
        instance = self.instance_id
        try:
            # Single fetch using the 3-strategy method
            ps_product = instance._fetch_single_product_full(self.prestashop_id)
            raw = json.dumps(ps_product, indent=2, ensure_ascii=False, default=str)
            self.write({'raw_data': raw})

            key_count = len(ps_product) if ps_product else 0
            if ps_product and key_count > 1:
                self._update_preview_from_ps_data(ps_product)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Raw Data Fetched (%d fields)') % key_count,
                    'message': _('Check the "Raw Data" and "Descriptions" tabs.'),
                    'type': 'info' if key_count > 2 else 'warning',
                    'sticky': False,
                },
            }
        except Exception as exc:
            self.write({
                'raw_data': 'ERROR: %s' % exc,
                'error_message': str(exc),
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Fetch Failed'),
                    'message': str(exc),
                    'type': 'danger',
                    'sticky': True,
                },
            }

    def action_import_single(self):
        """Import this single product from PrestaShop."""
        self.ensure_one()
        instance = self.instance_id
        try:
            self.write({'state': 'importing', 'error_message': False})
            self.env.cr.commit()

            existing = self.env['product.template'].search([
                ('prestashop_id', '=', self.prestashop_id),
                ('prestashop_instance_id', '=', instance.id),
            ], limit=1)

            ps_product = instance._fetch_single_product_full(self.prestashop_id)

            # Store raw data for debugging
            try:
                self.raw_data = json.dumps(
                    ps_product, indent=2, ensure_ascii=False, default=str,
                )
            except Exception:
                pass

            if not ps_product or not ps_product.get('id'):
                self.write({
                    'state': 'error',
                    'error_message': (
                        'Empty API response — product may have been deleted '
                        'or deactivated in PrestaShop (PS-%s). '
                        'Use "Debug Fetch" to see raw API response.'
                    ) % self.prestashop_id,
                })
                self.env.cr.commit()
                return

            # Update preview extra fields from PS data
            self._update_preview_from_ps_data(ps_product)

            product_tmpl = instance._sync_single_product(ps_product)
            self.write({
                'state': 'updated' if existing else 'imported',
                'imported_product_id': product_tmpl.id if product_tmpl else False,
                'import_date': fields.Datetime.now(),
                'name': product_tmpl.name if product_tmpl else self.name,
                'error_message': False,
            })
            self.env.cr.commit()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Product Imported'),
                    'message': self.name,
                    'type': 'success',
                    'sticky': False,
                },
            }

        except Exception as exc:
            self.write({'state': 'error', 'error_message': str(exc)})
            self.env.cr.commit()
            _logger.error("Import failed for PS-%s: %s", self.prestashop_id, exc)

    def action_import_selected(self):
        """Import selected preview records in background."""
        pending = self.filtered(lambda r: r.state in ('pending', 'error'))
        if not pending:
            raise UserError(_("No pending products to import in selection."))

        for instance in pending.mapped('instance_id'):
            previews = pending.filtered(lambda r: r.instance_id == instance)
            instance._import_previews_background(previews.ids)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Started'),
                'message': _(
                    '%d products are being imported in background. '
                    'The list refreshes automatically.'
                ) % len(pending),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_skip(self):
        self.write({'state': 'skipped'})

    def action_reset(self):
        self.write({'state': 'pending', 'error_message': False})

    def action_view_product(self):
        """Open the imported Odoo product."""
        self.ensure_one()
        if not self.imported_product_id:
            raise UserError(_("Product not yet imported."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': self.imported_product_id.id,
            'view_mode': 'form',
        }
