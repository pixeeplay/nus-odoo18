import base64
import json
import logging
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
import xml.sax.saxutils as saxutils

import requests

import odoo
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopInstance(models.Model):
    _inherit = 'prestashop.instance'

    # ------------------------------------
    # Product sync configuration fields
    # ------------------------------------
    last_product_sync_date = fields.Datetime('Last Product Sync', readonly=True)
    product_sync_limit = fields.Integer(
        'Products per Sync', default=0,
        help="Max products per sync run. 0 = all active products.",
    )
    sync_product_images = fields.Boolean('Sync Images', default=True)
    sync_product_features = fields.Boolean('Sync Features / Characteristics', default=True)
    sync_product_categories = fields.Boolean('Sync Categories', default=True)
    sync_product_stock = fields.Boolean('Sync Stock Quantities', default=True)
    default_product_categ_id = fields.Many2one(
        'product.category', 'Default Product Category',
    )
    product_ids = fields.One2many(
        'product.template', 'prestashop_instance_id', string='Synced Products',
    )
    product_count = fields.Integer('Product Count', compute='_compute_product_count')
    preview_ids = fields.One2many(
        'prestashop.product.preview', 'instance_id', string='Product Previews',
    )
    preview_count = fields.Integer('Preview Count', compute='_compute_preview_count')
    preview_pending_count = fields.Integer(
        'Pending', compute='_compute_preview_count',
    )
    product_sync_interval = fields.Integer(
        'Product Sync Interval (min)', default=60,
        help="Auto-sync interval for products in minutes. 0 = disabled.",
    )
    import_running = fields.Boolean('Import Running', default=False, readonly=True)
    product_sync_mode = fields.Selection([
        ('disabled', 'Disabled'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
    ], string='Auto-Sync Mode', default='disabled',
        help="Automatic product synchronization schedule.",
    )
    mapping_ids = fields.One2many(
        'prestashop.field.mapping', 'instance_id', string='Field Mappings',
    )
    mapping_count = fields.Integer('Mapping Count', compute='_compute_mapping_count')

    # ------------------------------------
    # Export configuration fields
    # ------------------------------------
    export_enabled = fields.Boolean('Enable Product Export', default=False)
    export_sync_mode = fields.Selection([
        ('disabled', 'Disabled'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
    ], string='Export Auto-Sync', default='disabled')
    export_running = fields.Boolean('Export Running', default=False, readonly=True)
    last_product_export_date = fields.Datetime('Last Product Export', readonly=True)
    last_stock_export_date = fields.Datetime('Last Stock Export', readonly=True)
    last_price_export_date = fields.Datetime('Last Price Export', readonly=True)
    stock_sync_mode = fields.Selection([
        ('disabled', 'Disabled'),
        ('every_15min', 'Every 15 Minutes'),
        ('hourly', 'Every Hour'),
    ], string='Stock Push Schedule', default='disabled')
    price_sync_mode = fields.Selection([
        ('disabled', 'Disabled'),
        ('hourly', 'Every Hour'),
        ('daily', 'Daily'),
    ], string='Price Push Schedule', default='disabled')
    export_default_ps_lang_id = fields.Integer(
        'PS Language ID', default=1,
        help="PrestaShop language ID for text fields (usually 1).",
    )
    export_default_active = fields.Boolean(
        'Export as Active', default=True,
        help="New products exported to PS will be active by default.",
    )
    export_images = fields.Boolean('Export Images', default=True)
    export_categories = fields.Boolean('Export Categories', default=True)
    export_variants = fields.Boolean('Export Variants', default=True)
    export_queue_ids = fields.One2many(
        'prestashop.export.queue', 'instance_id', string='Export Queue',
    )
    export_queue_count = fields.Integer(
        'Queue Count', compute='_compute_export_queue_count',
    )
    export_log_ids = fields.One2many(
        'prestashop.export.log', 'instance_id', string='Export Logs',
    )

    @api.depends('export_queue_ids')
    def _compute_export_queue_count(self):
        for rec in self:
            rec.export_queue_count = self.env['prestashop.export.queue'].search_count([
                ('instance_id', '=', rec.id),
                ('state', '=', 'pending'),
            ])

    @api.depends('mapping_ids')
    def _compute_mapping_count(self):
        for rec in self:
            rec.mapping_count = len(rec.mapping_ids)

    @api.depends('product_ids')
    def _compute_product_count(self):
        for rec in self:
            rec.product_count = len(rec.product_ids)

    @api.depends('preview_ids', 'preview_ids.state')
    def _compute_preview_count(self):
        for rec in self:
            rec.preview_count = len(rec.preview_ids)
            rec.preview_pending_count = len(
                rec.preview_ids.filtered(lambda p: p.state in ('pending', 'error'))
            )

    # ------------------------------------
    # Field mapping helpers
    # ------------------------------------
    def action_generate_mappings(self):
        """Generate or reset the default field mappings."""
        self.ensure_one()
        self.env['prestashop.field.mapping']._create_defaults_for_instance(self.id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Mappings Generated'),
                'message': _('Default field mappings have been created.'),
                'type': 'success',
                'sticky': False,
            },
        }

    def _is_mapping_active(self, ps_field, odoo_field=None):
        """Check if a field mapping is active for this instance.

        If no mappings exist yet, defaults to True (all fields active).
        """
        if not self.mapping_ids:
            return True
        domain = [
            ('instance_id', '=', self.id),
            ('ps_field_name', '=', ps_field),
        ]
        if odoo_field:
            domain.append(('odoo_field_name', '=', odoo_field))
        mapping = self.env['prestashop.field.mapping'].search(domain, limit=1)
        if not mapping:
            return True  # no mapping defined = active by default
        return mapping.active

    # ------------------------------------
    # Helpers – multi-language text
    # ------------------------------------
    def _get_ps_text(self, field_data, lang_id=1):
        """Extract text value from a PrestaShop multi-language field.

        PrestaShop JSON API returns multi-language fields in MANY formats
        depending on PS version, webservice config, and XML→JSON conversion:

         1. Plain string:  "text"
         2. Integer / number:  0
         3. List of dicts:  [{"id":"1","value":"text"}, ...]
         4. Dict w/ language list:
            {"language": [{"attrs":{"id":"1"},"value":"text"}, ...]}
         5. Dict w/ single language:
            {"language": {"attrs":{"id":"1"},"value":"text"}}
         6. Dict w/ simple id/value:
            {"language": {"id":"1","value":"text"}}
         7. Dict w/ just "value":  {"value": "text"}
         8. XML-to-JSON #text style:
            {"language": {"@attributes":{"id":"1"},"#text":"text"}}
         9. XML-to-JSON $ style:
            {"language": [{"id":"1","$":"text"}]}
        10. Nested value:
            {"language": [{"id":"1","value":{"#text":"text"}}]}
        """
        if field_data is None or field_data == '':
            return ''
        if isinstance(field_data, (int, float)):
            return str(field_data) if field_data else ''
        if isinstance(field_data, str):
            return field_data

        lang_str = str(lang_id)

        def _extract_value(item):
            """Extract text from a single language dict, trying all known keys."""
            if isinstance(item, str):
                return item
            if not isinstance(item, dict):
                return ''
            # Try 'value' first (most common)
            val = item.get('value')
            if val is not None:
                if isinstance(val, dict):
                    # Nested: {"value": {"#text": "..."}}
                    return str(val.get('#text', val.get('$', '')) or '')
                if val or val == 0:
                    return str(val)
            # Try '#text' (simplexml / XML-to-JSON)
            val = item.get('#text')
            if val:
                return str(val)
            # Try '$' (some converters)
            val = item.get('$')
            if val:
                return str(val)
            return ''

        def _extract_id(item):
            """Extract language ID from a dict, trying all known keys."""
            if not isinstance(item, dict):
                return ''
            # Direct 'id'
            item_id = item.get('id')
            if item_id is not None:
                return str(item_id)
            # attrs.id
            attrs = item.get('attrs')
            if isinstance(attrs, dict) and 'id' in attrs:
                return str(attrs['id'])
            # @attributes.id (simplexml)
            attrs = item.get('@attributes')
            if isinstance(attrs, dict) and 'id' in attrs:
                return str(attrs['id'])
            return ''

        def _search_items(items):
            """Search a list of language items for the best match."""
            if not isinstance(items, list):
                return None
            # 1) Exact language match
            for item in items:
                if _extract_id(item) == lang_str:
                    val = _extract_value(item)
                    if val:
                        return val
            # 2) First item with any value
            for item in items:
                val = _extract_value(item)
                if val:
                    return val
            # 3) First plain string
            for item in items:
                if isinstance(item, str) and item:
                    return item
            return None

        # --- list at top level ---
        if isinstance(field_data, list):
            result = _search_items(field_data)
            return result or ''

        # --- dict ---
        if isinstance(field_data, dict):
            langs = field_data.get('language')
            if langs is not None:
                if isinstance(langs, str):
                    return langs
                if isinstance(langs, dict):
                    # Single language entry
                    if _extract_id(langs) == lang_str or not lang_str:
                        val = _extract_value(langs)
                        if val:
                            return val
                    # Even if ID doesn't match, take the value
                    val = _extract_value(langs)
                    if val:
                        return val
                if isinstance(langs, list):
                    result = _search_items(langs)
                    if result:
                        return result

            # No "language" key — try direct value extraction
            val = _extract_value(field_data)
            if val:
                return val

            return ''

        return str(field_data)

    # ------------------------------------
    # Helpers – long-timeout API call
    # ------------------------------------
    def _api_get_long(self, resource, resource_id=None, params=None, timeout=120):
        """Same as _api_get but with configurable (longer) timeout."""
        self.ensure_one()
        base_url = self._get_base_url()
        if resource_id:
            url = f"{base_url}/{resource}/{resource_id}"
        else:
            url = f"{base_url}/{resource}"
        if params is None:
            params = {}
        params['output_format'] = 'JSON'
        _logger.info("PS API (long) call: %s", url)
        resp = requests.get(url, auth=(self.api_key, ''), params=params, timeout=timeout)
        if resp.status_code != 200:
            _logger.error("PS API error %s: %s", resp.status_code, resp.text[:500])
            raise UserError(_("PrestaShop API error (status %s). URL: %s") % (resp.status_code, url))
        try:
            return resp.json()
        except Exception:
            raise UserError(_("Invalid API response (not JSON). URL: %s") % url)

    # ------------------------------------
    # Helpers – image download
    # ------------------------------------
    def _download_image(self, product_id, image_id):
        """Download a product image from PrestaShop, return base64 bytes or False."""
        self.ensure_one()
        base_url = self._get_base_url()
        url = f"{base_url}/images/products/{product_id}/{image_id}"
        try:
            resp = requests.get(url, auth=(self.api_key, ''), timeout=60)
            if resp.status_code == 200 and resp.content:
                return base64.b64encode(resp.content)
        except Exception as exc:
            _logger.warning(
                "Image download failed (product %s, image %s): %s",
                product_id, image_id, exc,
            )
        return False

    # ------------------------------------
    # Helpers – categories
    # ------------------------------------
    def _get_or_create_category(self, category_id):
        """Resolve a PrestaShop category ID to an Odoo product.category."""
        self.ensure_one()
        cat_str = str(category_id)
        if not cat_str or cat_str in ('0', '1', '2'):
            return self.default_product_categ_id or self.env.ref('product.product_category_all')

        existing = self.env['product.category'].search([
            ('prestashop_id', '=', cat_str),
            ('prestashop_instance_id', '=', self.id),
        ], limit=1)
        if existing:
            return existing

        try:
            cat = self._api_get_via_list('categories', cat_str, 'category')
            name = self._get_ps_text(cat.get('name', ''))
            if not name:
                name = f'PS Category {cat_str}'

            parent_ps = str(cat.get('id_parent', '0'))
            if parent_ps and parent_ps not in ('0', '1', '2'):
                parent = self._get_or_create_category(parent_ps)
            else:
                parent = self.default_product_categ_id or self.env.ref('product.product_category_all')

            new_cat = self.env['product.category'].create({
                'name': name,
                'parent_id': parent.id,
                'prestashop_id': cat_str,
                'prestashop_instance_id': self.id,
            })
            _logger.info("Created category '%s' (PS ID %s)", name, cat_str)
            return new_cat
        except Exception as exc:
            _logger.warning("Error syncing category %s: %s", cat_str, exc)
            return self.default_product_categ_id or self.env.ref('product.product_category_all')

    # ------------------------------------
    # Helpers – manufacturer / brand
    # ------------------------------------
    def _get_manufacturer_name(self, manufacturer_id):
        try:
            if not manufacturer_id or str(manufacturer_id) == '0':
                return ''
            mfr = self._api_get_via_list('manufacturers', str(manufacturer_id), 'manufacturer')
            return mfr.get('name', '') or ''
        except Exception:
            return ''

    # ------------------------------------
    # Helpers – tax resolution
    # ------------------------------------
    def _resolve_tax_rate(self, tax_rules_group_id):
        """Resolve a PrestaShop tax_rules_group ID to (rate, group_name).

        Chain: tax_rule_groups → tax_rules → taxes
        Uses list endpoints with filter[id] (View permission may be missing).
        Returns (float rate, str group_name).
        """
        self.ensure_one()
        group_name = ''
        rate = 0.0
        try:
            # Get the tax rules group name (via list endpoint)
            grp = self._api_get_via_list('tax_rule_groups', str(tax_rules_group_id), 'tax_rule_group')
            group_name = self._get_ps_text(grp.get('name', ''))

            # Get tax rules for this group (already uses list endpoint)
            rules_data = self._api_get_long(
                'tax_rules', params={
                    'filter[id_tax_rules_group]': str(tax_rules_group_id),
                    'display': '[id_tax]',
                    'limit': '1',
                }, timeout=30,
            )
            rules = rules_data.get('tax_rules', [])
            if isinstance(rules, dict):
                rules = [rules]
            if rules:
                tax_id_ps = str(rules[0].get('id_tax', '0'))
                if tax_id_ps and tax_id_ps != '0':
                    # Get tax rate (via list endpoint)
                    tax = self._api_get_via_list('taxes', tax_id_ps, 'tax')
                    rate = float(tax.get('rate', 0) or 0)
        except Exception as exc:
            _logger.warning(
                "Tax resolution failed for group %s: %s",
                tax_rules_group_id, exc,
            )
        return rate, group_name

    def _find_odoo_tax(self, rate, company=None):
        """Find an Odoo sale tax matching the given rate (percentage)."""
        self.ensure_one()
        if not rate:
            return self.env['account.tax']
        if not company:
            company = self.env.company
        tax = self.env['account.tax'].search([
            ('type_tax_use', '=', 'sale'),
            ('amount', '=', rate),
            ('company_id', '=', company.id),
        ], limit=1)
        if not tax:
            # Try approximate match (within 0.1%)
            tax = self.env['account.tax'].search([
                ('type_tax_use', '=', 'sale'),
                ('amount', '>=', rate - 0.1),
                ('amount', '<=', rate + 0.1),
                ('company_id', '=', company.id),
            ], limit=1)
        return tax or self.env['account.tax']

    # ------------------------------------
    # Helpers – features / characteristics
    # ------------------------------------
    def _get_feature_name(self, feature_id):
        try:
            feat = self._api_get_via_list('product_features', str(feature_id), 'product_feature')
            return self._get_ps_text(feat.get('name', ''))
        except Exception:
            return f'Feature {feature_id}'

    def _get_feature_value_text(self, value_id):
        try:
            fval = self._api_get_via_list('product_feature_values', str(value_id), 'product_feature_value')
            return self._get_ps_text(fval.get('value', ''))
        except Exception:
            return f'Value {value_id}'

    def _sync_product_features_to_odoo(self, product_tmpl, feature_list):
        """Map PrestaShop product_features to Odoo product attributes (no_variant)."""
        if not feature_list:
            return

        Attribute = self.env['product.attribute']
        AttrValue = self.env['product.attribute.value']
        PTAL = self.env['product.template.attribute.line']

        for feat in feature_list:
            if not isinstance(feat, dict):
                continue
            feat_id = str(feat.get('id', ''))
            val_id = str(feat.get('id_feature_value', ''))
            if not feat_id or not val_id:
                continue

            feat_name = self._get_feature_name(feat_id)
            feat_value = self._get_feature_value_text(val_id)
            if not feat_name or not feat_value:
                continue

            # Attribute
            attribute = Attribute.search([('name', '=', feat_name)], limit=1)
            if not attribute:
                attribute = Attribute.create({
                    'name': feat_name,
                    'create_variant': 'no_variant',
                    'display_type': 'radio',
                })

            # Attribute value
            attr_val = AttrValue.search([
                ('name', '=', feat_value),
                ('attribute_id', '=', attribute.id),
            ], limit=1)
            if not attr_val:
                attr_val = AttrValue.create({
                    'name': feat_value,
                    'attribute_id': attribute.id,
                })

            # Attribute line on product
            attr_line = PTAL.search([
                ('product_tmpl_id', '=', product_tmpl.id),
                ('attribute_id', '=', attribute.id),
            ], limit=1)
            if attr_line:
                if attr_val.id not in attr_line.value_ids.ids:
                    attr_line.write({'value_ids': [(4, attr_val.id)]})
            else:
                PTAL.create({
                    'product_tmpl_id': product_tmpl.id,
                    'attribute_id': attribute.id,
                    'value_ids': [(6, 0, [attr_val.id])],
                })

    # ------------------------------------
    # Helpers – product images
    # ------------------------------------
    def _sync_product_images_to_odoo(self, product_tmpl, ps_product_id, image_ids):
        """Download images from PrestaShop and attach them to the product."""
        if not image_ids:
            return

        if isinstance(image_ids, dict):
            image_ids = [image_ids]

        first = True
        for img in image_ids:
            img_id = str(img.get('id', '')) if isinstance(img, dict) else str(img)
            if not img_id:
                continue

            b64 = self._download_image(ps_product_id, img_id)
            if not b64:
                continue

            if first:
                product_tmpl.write({'image_1920': b64})
                first = False
            else:
                tag = f'PS-{ps_product_id}-{img_id}'
                existing = self.env['product.image'].search([
                    ('product_tmpl_id', '=', product_tmpl.id),
                    ('name', '=', tag),
                ], limit=1)
                if not existing:
                    self.env['product.image'].create({
                        'product_tmpl_id': product_tmpl.id,
                        'name': tag,
                        'image_1920': b64,
                    })

    # ------------------------------------
    # Helpers – stock quantity
    # ------------------------------------
    def _sync_product_stock(self, product_tmpl, ps_product_id):
        """Fetch stock quantity from PrestaShop and update Odoo."""
        try:
            data = self._api_get_long(
                'stock_availables', params={
                    'filter[id_product]': str(ps_product_id),
                    'filter[id_product_attribute]': '0',
                    'display': '[quantity]',
                }, timeout=30,
            )
            stocks = data.get('stock_availables', [])
            if isinstance(stocks, dict):
                stocks = [stocks]
            if stocks:
                qty = int(stocks[0].get('quantity', 0) or 0)
                # Update the qty_available via stock.quant
                product = product_tmpl.product_variant_id
                if product and qty > 0:
                    warehouse = self.warehouse_id
                    location = warehouse.lot_stock_id if warehouse else False
                    if location:
                        quant = self.env['stock.quant'].search([
                            ('product_id', '=', product.id),
                            ('location_id', '=', location.id),
                        ], limit=1)
                        if quant:
                            quant.sudo().write({'quantity': qty})
                        else:
                            self.env['stock.quant'].sudo().create({
                                'product_id': product.id,
                                'location_id': location.id,
                                'quantity': qty,
                            })
                        _logger.info(
                            "Stock updated for '%s' (PS-%s): %d",
                            product_tmpl.name, ps_product_id, qty,
                        )
                return qty
        except Exception as exc:
            _logger.warning("Stock sync failed for PS-%s: %s", ps_product_id, exc)
        return 0

    # ------------------------------------
    # Core – sync a single product
    # ------------------------------------
    def _sync_single_product(self, ps_product):
        """Import / update a single PrestaShop product into Odoo."""
        self.ensure_one()
        ps_id = str(ps_product.get('id', ''))
        if not ps_id:
            return None

        # --- Debug: log all top-level keys and their types ---
        _logger.info(
            "PS-%s: syncing product. Top-level keys: %s",
            ps_id,
            {k: type(v).__name__ for k, v in ps_product.items()},
        )

        # --- text fields ---
        raw_name = ps_product.get('name', '')
        name = self._get_ps_text(raw_name)
        if not name:
            _logger.warning(
                "PS-%s: name field EMPTY after parsing. "
                "Raw type=%s, repr=%r. "
                "Trying all top-level keys for a name...",
                ps_id, type(raw_name).__name__, raw_name,
            )
            # Last resort: try to find any 'name' nested differently
            for key in ('name', 'meta_title', 'link_rewrite'):
                attempt = self._get_ps_text(ps_product.get(key, ''))
                if attempt:
                    name = attempt
                    _logger.info("PS-%s: recovered name from '%s': %s", ps_id, key, name)
                    break

        description = self._get_ps_text(ps_product.get('description', ''))
        description_short = self._get_ps_text(ps_product.get('description_short', ''))
        reference = str(ps_product.get('reference', '') or '')
        ean13 = str(ps_product.get('ean13', '') or '')
        price = float(ps_product.get('price', 0) or 0)
        wholesale_price = float(ps_product.get('wholesale_price', 0) or 0)
        weight = float(ps_product.get('weight', 0) or 0)
        id_category_default = str(ps_product.get('id_category_default', '0'))
        id_manufacturer = str(ps_product.get('id_manufacturer', '0'))
        meta_title = self._get_ps_text(ps_product.get('meta_title', ''))
        meta_description = self._get_ps_text(ps_product.get('meta_description', ''))
        link_rewrite = self._get_ps_text(ps_product.get('link_rewrite', ''))
        ps_active = str(ps_product.get('active', '1')) == '1'
        associations = ps_product.get('associations', {}) or {}

        # Debug: log parsed values
        _logger.info(
            "PS-%s parsed: name=%r, ref=%r, price=%s, ean=%r, "
            "desc_len=%d, short_len=%d, manufacturer_id=%s, "
            "category_id=%s, active=%s, associations_keys=%s",
            ps_id, name, reference, price, ean13,
            len(description or ''), len(description_short or ''),
            id_manufacturer, id_category_default, ps_active,
            list(associations.keys()) if associations else [],
        )

        # Build the public product URL
        store_url = self.url.rstrip('/').replace('/api', '')
        product_url = f"{store_url}/{link_rewrite}" if link_rewrite else ''

        # --- lookup existing ---
        product_tmpl = self.env['product.template'].search([
            ('prestashop_id', '=', ps_id),
            ('prestashop_instance_id', '=', self.id),
        ], limit=1)
        if not product_tmpl and reference:
            product_tmpl = self.env['product.template'].search([
                ('default_code', '=', reference),
            ], limit=1)

        # --- category (respects mapping) ---
        categ_id = False
        if (self.sync_product_categories
                and id_category_default
                and self._is_mapping_active('id_category_default')):
            categ = self._get_or_create_category(id_category_default)
            if categ:
                categ_id = categ.id

        # --- manufacturer (respects mapping) ---
        manufacturer = ''
        if self._is_mapping_active('id_manufacturer'):
            manufacturer = self._get_manufacturer_name(id_manufacturer)

        # --- prepare vals (always set PS tracking fields) ---
        vals = {
            'prestashop_id': ps_id,
            'prestashop_instance_id': self.id,
            'prestashop_last_sync': fields.Datetime.now(),
            'type': 'consu',
        }

        # Map each field respecting active mappings
        if self._is_mapping_active('name'):
            vals['name'] = name or f'Product PS-{ps_id}'
        if self._is_mapping_active('reference'):
            vals['default_code'] = reference or False
        if self._is_mapping_active('price'):
            vals['list_price'] = price
        if self._is_mapping_active('wholesale_price'):
            vals['standard_price'] = wholesale_price
        if self._is_mapping_active('weight'):
            vals['weight'] = weight
        if self._is_mapping_active('description', 'description'):
            vals['description'] = description or False
        if self._is_mapping_active('description_short', 'description_sale'):
            vals['description_sale'] = description_short or False
        if self._is_mapping_active('description', 'prestashop_description_html'):
            vals['prestashop_description_html'] = description or False
        if self._is_mapping_active('description_short', 'prestashop_description_short_html'):
            vals['prestashop_description_short_html'] = description_short or False
        if self._is_mapping_active('meta_title'):
            vals['prestashop_meta_title'] = meta_title or False
        if self._is_mapping_active('meta_description'):
            vals['prestashop_meta_description'] = meta_description or False
        if self._is_mapping_active('link_rewrite'):
            vals['prestashop_url'] = product_url or False
        if self._is_mapping_active('id_manufacturer'):
            vals['prestashop_manufacturer'] = manufacturer or False
        if self._is_mapping_active('active'):
            vals['prestashop_active'] = ps_active

        # barcode / ean13
        if self._is_mapping_active('ean13'):
            vals['prestashop_ean13'] = ean13 or False
            if ean13 and len(ean13) in (8, 12, 13, 14):
                dup = self.env['product.template'].search([
                    ('barcode', '=', ean13),
                    ('id', '!=', product_tmpl.id if product_tmpl else 0),
                ], limit=1)
                if not dup:
                    vals['barcode'] = ean13

        if categ_id:
            vals['categ_id'] = categ_id

        # --- eco-tax (respects mapping) ---
        if self._is_mapping_active('ecotax'):
            try:
                vals['prestashop_ecotax'] = float(ps_product.get('ecotax', 0) or 0)
            except (ValueError, TypeError):
                pass

        # --- taxes (respects mapping) ---
        if self._is_mapping_active('id_tax_rules_group'):
            tax_group_id = str(ps_product.get('id_tax_rules_group', '0'))
            if tax_group_id and tax_group_id != '0':
                vals['prestashop_tax_rules_group_id'] = tax_group_id
                try:
                    rate, _group_name = self._resolve_tax_rate(tax_group_id)
                    vals['prestashop_tax_rate'] = rate
                    odoo_tax = self._find_odoo_tax(rate)
                    if odoo_tax:
                        vals['prestashop_tax_id'] = odoo_tax.id
                except Exception as exc:
                    _logger.warning("Tax mapping failed for PS-%s: %s", ps_id, exc)

        # --- create or update ---
        if product_tmpl:
            product_tmpl.write(vals)
            _logger.info("Updated product '%s' (PS ID %s)", vals.get('name', '?'), ps_id)
        else:
            product_tmpl = self.env['product.template'].create(vals)
            _logger.info("Created product '%s' (PS ID %s)", vals.get('name', '?'), ps_id)

        # --- images (respects mapping) ---
        if (self.sync_product_images
                and self._is_mapping_active('associations.images')):
            image_list = self._normalize_association_list(associations, 'images', 'image')
            _logger.info(
                "PS-%s: %d images to sync (raw images type: %s)",
                ps_id, len(image_list),
                type(associations.get('images')).__name__ if associations.get('images') else 'None',
            )
            self._sync_product_images_to_odoo(product_tmpl, ps_id, image_list)

        # --- features / characteristics (respects mapping) ---
        if (self.sync_product_features
                and self._is_mapping_active('associations.product_features')):
            feat_list = self._normalize_association_list(associations, 'product_features', 'product_feature')
            self._sync_product_features_to_odoo(product_tmpl, feat_list)

        # --- stock (respects mapping) ---
        if (self.sync_product_stock
                and self._is_mapping_active('stock_availables')):
            self._sync_product_stock(product_tmpl, ps_id)

        return product_tmpl

    # =============================================
    # Product Preview – Fetch & Import
    # =============================================

    def action_fetch_product_previews(self):
        """Fetch all active products from PrestaShop into preview records.

        This is a fast, lightweight call that creates preview records so
        the user can SEE what's in PrestaShop before importing.
        """
        self.ensure_one()

        # Step 1 – fetch lightweight product list with basic fields
        try:
            params = {
                'display': '[id,name,reference,price,ean13,active]',
                'filter[active]': '[1]',
                'sort': '[id_ASC]',
            }
            if self.product_sync_limit and self.product_sync_limit > 0:
                params['limit'] = self.product_sync_limit

            data = self._api_get_long('products', params=params, timeout=60)
            products = data.get('products', [])
            if isinstance(products, dict):
                products = [products]
        except Exception as exc:
            raise UserError(
                _("Failed to fetch products from PrestaShop: %s") % exc
            )

        if not products:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Products'),
                    'message': _('No active products found in PrestaShop.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        Preview = self.env['prestashop.product.preview']
        created = skipped = 0

        for ps_prod in products:
            ps_id = str(ps_prod.get('id', ''))
            if not ps_id:
                continue

            # Already in preview?
            existing_preview = Preview.search([
                ('instance_id', '=', self.id),
                ('prestashop_id', '=', ps_id),
            ], limit=1)
            if existing_preview:
                # Update name/price if changed
                name = self._get_ps_text(ps_prod.get('name', ''))
                # Validate — lightweight fetch can return raw JSON
                if name and (name.startswith('{') or name.startswith('[') or len(name) > 500):
                    name = ''
                ref = ps_prod.get('reference', '') or ''
                price = float(ps_prod.get('price', 0) or 0)
                if name and existing_preview.name != name:
                    existing_preview.write({
                        'name': name,
                        'reference': ref,
                        'price': price,
                    })
                skipped += 1
                continue

            name = self._get_ps_text(ps_prod.get('name', ''))
            # Validate — lightweight fetch can return raw JSON for multi-lang
            if not name or name.startswith('{') or name.startswith('[') or len(name) > 500:
                name = ''  # Will be corrected during full import (display=full)
            reference = ps_prod.get('reference', '') or ''
            price = float(ps_prod.get('price', 0) or 0)
            ean13 = ps_prod.get('ean13', '') or ''
            active_in_ps = str(ps_prod.get('active', '1')) == '1'

            # Check if already imported in Odoo
            odoo_product = self.env['product.template'].search([
                ('prestashop_id', '=', ps_id),
                ('prestashop_instance_id', '=', self.id),
            ], limit=1)

            Preview.create({
                'instance_id': self.id,
                'prestashop_id': ps_id,
                'name': name or f'PS-{ps_id}',
                'reference': reference,
                'price': price,
                'ean13': ean13,
                'active_in_ps': active_in_ps,
                'state': 'imported' if odoo_product else 'pending',
                'imported_product_id': odoo_product.id if odoo_product else False,
            })
            created += 1

            if created % 20 == 0:
                self.env.cr.commit()

        self.env.cr.commit()

        # Open the preview list
        return {
            'type': 'ir.actions.act_window',
            'name': _('PrestaShop Products - Preview (%d new, %d existing)') % (created, skipped),
            'res_model': 'prestashop.product.preview',
            'view_mode': 'list,form',
            'domain': [('instance_id', '=', self.id)],
            'context': {
                'default_instance_id': self.id,
                'search_default_filter_to_import': 1,
            },
        }

    def action_import_all_previews(self):
        """Import all pending preview products in background."""
        self.ensure_one()
        previews = self.env['prestashop.product.preview'].search([
            ('instance_id', '=', self.id),
            ('state', 'in', ('pending', 'error')),
        ])
        if not previews:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nothing to Import'),
                    'message': _('No pending products to import. Fetch products first.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        self._import_previews_background(previews.ids)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Started'),
                'message': _(
                    '%d products are being imported in background. '
                    'The list will refresh automatically.'
                ) % len(previews),
                'type': 'info',
                'sticky': True,
            },
        }

    def action_open_product_previews(self):
        """Open the product preview list for this instance."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('PrestaShop Product Preview'),
            'res_model': 'prestashop.product.preview',
            'view_mode': 'list,form',
            'domain': [('instance_id', '=', self.id)],
            'context': {
                'default_instance_id': self.id,
            },
        }

    def _import_previews_background(self, preview_ids):
        """Run the import of preview products in a background thread."""
        self.ensure_one()
        db_name = self.env.cr.dbname
        uid = self.env.uid
        instance_id = self.id

        # Mark as running
        self.import_running = True
        self.env.cr.commit()

        def _run():
            try:
                db_registry = odoo.registry(db_name)
                with db_registry.cursor() as cr:
                    env = odoo.api.Environment(cr, uid, {})
                    instance = env['prestashop.instance'].browse(instance_id)
                    Preview = env['prestashop.product.preview']
                    previews = Preview.browse(preview_ids).exists()

                    total = len(previews)
                    created = updated = errors = 0

                    for idx, preview in enumerate(previews, 1):
                        try:
                            preview.write({
                                'state': 'importing',
                                'error_message': False,
                            })
                            cr.commit()

                            # Send progress notification
                            env['bus.bus']._sendone(
                                env.user.partner_id,
                                'simple_notification',
                                {
                                    'title': 'Import %d/%d' % (idx, total),
                                    'message': '%s [%s]' % (
                                        preview.name or '',
                                        preview.reference or '',
                                    ),
                                    'type': 'info',
                                    'sticky': False,
                                },
                            )
                            cr.commit()

                            # Check if already exists
                            existing = env['product.template'].search([
                                ('prestashop_id', '=', preview.prestashop_id),
                                ('prestashop_instance_id', '=', instance.id),
                            ], limit=1)

                            # Fetch full product data from PS
                            ps_product = instance._fetch_single_product_full(
                                preview.prestashop_id
                            )
                            if not ps_product or not ps_product.get('id'):
                                preview.write({
                                    'state': 'error',
                                    'error_message': (
                                        'Empty API response — product may '
                                        'have been deleted or deactivated '
                                        'in PrestaShop (PS-%s)'
                                    ) % preview.prestashop_id,
                                })
                                errors += 1
                                cr.commit()
                                continue

                            # Update preview extra fields
                            preview._update_preview_from_ps_data(ps_product)

                            # Sync into Odoo
                            product_tmpl = instance._sync_single_product(ps_product)

                            new_name = product_tmpl.name if product_tmpl else preview.name
                            if existing:
                                updated += 1
                                state = 'updated'
                            else:
                                created += 1
                                state = 'imported'

                            preview.write({
                                'state': state,
                                'imported_product_id': (
                                    product_tmpl.id if product_tmpl else False
                                ),
                                'import_date': fields.Datetime.now(),
                                'name': new_name,
                                'error_message': False,
                            })
                            cr.commit()

                            # Notify: success
                            env['bus.bus']._sendone(
                                env.user.partner_id,
                                'simple_notification',
                                {
                                    'title': '%s %d/%d' % (
                                        'Updated' if existing else 'Created',
                                        idx, total,
                                    ),
                                    'message': new_name,
                                    'type': 'success',
                                    'sticky': False,
                                },
                            )
                            cr.commit()

                        except Exception as exc:
                            errors += 1
                            try:
                                preview.write({
                                    'state': 'error',
                                    'error_message': str(exc),
                                })
                                cr.commit()
                            except Exception:
                                cr.rollback()
                            _logger.error(
                                "BG import error PS-%s: %s",
                                preview.prestashop_id, exc,
                            )

                    # Done – final notification
                    instance.write({
                        'last_product_sync_date': fields.Datetime.now(),
                        'import_running': False,
                    })
                    cr.commit()

                    env['bus.bus']._sendone(
                        env.user.partner_id,
                        'simple_notification',
                        {
                            'title': 'Import Complete!',
                            'message': (
                                'Created: %d | Updated: %d | Errors: %d'
                            ) % (created, updated, errors),
                            'type': 'success' if not errors else 'warning',
                            'sticky': True,
                        },
                    )
                    cr.commit()

            except Exception as exc:
                _logger.error("Background import thread failed: %s", exc)
                try:
                    db_registry = odoo.registry(db_name)
                    with db_registry.cursor() as cr:
                        env = odoo.api.Environment(cr, uid, {})
                        instance = env['prestashop.instance'].browse(instance_id)
                        instance.import_running = False
                        cr.commit()
                except Exception:
                    pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    # ------------------------------------
    # Actions (legacy, kept for backward compat)
    # ------------------------------------
    def _fetch_product_ids(self):
        """Fetch the list of active product IDs from PrestaShop (lightweight call)."""
        self.ensure_one()
        params = {
            'display': '[id]',
            'filter[active]': '[1]',
            'sort': '[id_ASC]',
        }
        if self.product_sync_limit and self.product_sync_limit > 0:
            params['limit'] = self.product_sync_limit

        data = self._api_get_long('products', params=params, timeout=60)
        products = data.get('products', [])
        if isinstance(products, dict):
            products = [products]
        return [str(p.get('id', '')) for p in products if p.get('id')]

    # Explicit field list (fallback — no associations)
    _PS_PRODUCT_FIELDS = (
        'id,name,description,description_short,price,wholesale_price,'
        'reference,ean13,weight,active,id_category_default,'
        'id_manufacturer,meta_title,meta_description,link_rewrite,'
        'ecotax,id_tax_rules_group'
    )

    def _extract_product_from_response(self, data):
        """Extract product dict from a PS API response (handles all formats).

        Handles:
        - {"product": {...}}           (single resource response)
        - {"products": [{...}]}        (list response)
        - {"products": {"product": [{...}]}}  (list with nested key)
        - [{...}]                      (raw list at top level)
        - data is None / empty
        """
        if not data:
            return {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get('id'):
                    return item
            return {}
        if not isinstance(data, dict):
            return {}
        product = data.get('product')
        if isinstance(product, dict) and product:
            return product
        products = data.get('products')
        if isinstance(products, list) and products:
            return products[0] if isinstance(products[0], dict) else {}
        if isinstance(products, dict):
            # Could be {"products": {"product": [{...}]}} or {"products": {...}}
            nested = products.get('product')
            if isinstance(nested, list) and nested:
                return nested[0] if isinstance(nested[0], dict) else {}
            if isinstance(nested, dict):
                return nested
            # Treat the dict itself as the product
            if products.get('id'):
                return products
        return {}

    @staticmethod
    def _normalize_association_list(associations, key, nested_key=None):
        """Normalize an associations entry to always return a list of dicts.

        PrestaShop returns associations in two formats depending on the endpoint:
        - List endpoint (display=full): {"images": [{"id": "123"}, ...]}
        - Single resource endpoint:     {"images": {"image": [{"id": "123"}, ...]}}

        This helper handles both, returning a flat list of dicts in all cases.

        :param associations: the full associations dict from ps_product
        :param key: top-level key, e.g. 'images', 'product_features'
        :param nested_key: inner key for single-resource format, e.g. 'image', 'product_feature'.
                          If None, defaults to key without trailing 's'.
        """
        if not associations or not isinstance(associations, dict):
            return []
        entry = associations.get(key)
        if entry is None:
            return []
        # Format A: directly a list (list endpoint with display=full)
        if isinstance(entry, list):
            return [item for item in entry if isinstance(item, dict)]
        # Format B: dict with nested key (single resource endpoint)
        if isinstance(entry, dict):
            if nested_key is None:
                nested_key = key.rstrip('s') if key.endswith('s') else key
            inner = entry.get(nested_key)
            if isinstance(inner, list):
                return [item for item in inner if isinstance(item, dict)]
            if isinstance(inner, dict):
                return [inner]
            # Maybe the dict itself has 'id' → treat as single item
            if entry.get('id'):
                return [entry]
        return []

    def _api_get_via_list(self, resource, resource_id, singular_key=None):
        """Fetch a single resource via the list endpoint + filter[id].

        The API key has List permission but NOT View permission, so
        /api/resource/ID fails but /api/resource?filter[id]=ID&display=full works.

        :param resource: e.g. 'categories', 'manufacturers'
        :param resource_id: the PS ID to fetch
        :param singular_key: key in response for single item, e.g. 'category'.
                            If None, tries resource without trailing 's'.
        :returns: the inner resource dict, or {}
        """
        self.ensure_one()
        try:
            data = self._api_get_long(
                resource, params={
                    'display': 'full',
                    'filter[id]': str(resource_id),
                }, timeout=30,
            )
            if not data or not isinstance(data, dict):
                return {}
            # Try singular key first: {"category": {...}}
            if singular_key is None:
                singular_key = resource.rstrip('s') if resource.endswith('s') else resource
            item = data.get(singular_key)
            if isinstance(item, dict):
                return item
            # Try plural key: {"categories": [{...}]}
            items = data.get(resource)
            if isinstance(items, list) and items:
                return items[0] if isinstance(items[0], dict) else {}
            if isinstance(items, dict):
                nested = items.get(singular_key)
                if isinstance(nested, list) and nested:
                    return nested[0] if isinstance(nested[0], dict) else {}
                if isinstance(nested, dict):
                    return nested
                if items.get('id'):
                    return items
        except Exception as exc:
            _logger.warning("_api_get_via_list(%s, %s) failed: %s", resource, resource_id, exc)
        return {}

    def _fetch_single_product_full(self, ps_product_id):
        """Fetch full details for a single product by ID.

        Uses LIST endpoint + filter[id] instead of single resource endpoint
        because the API key has List permission but not View permission.

        Diagnostic confirmed:
        - List + filter + display=full  → 70 keys (with associations)
        - List + filter + explicit fields → 17 keys (no associations)
        - Single resource (any) → 1 key only (FAILS)
        """
        self.ensure_one()
        ps_id = str(ps_product_id)

        # Strategy 1: LIST + filter + display=full (70 keys, includes associations)
        try:
            data = self._api_get_long(
                'products', params={
                    'display': 'full',
                    'filter[id]': str(ps_id),
                },
                timeout=120,
            )
            product = self._extract_product_from_response(data)
            if product and len(product) > 2:
                _logger.info("PS-%s: fetched OK (%d keys)", ps_id, len(product))
                return product
            _logger.warning("PS-%s: display=full returned only %d keys, trying explicit fields...",
                            ps_id, len(product))
        except Exception as exc:
            _logger.warning("PS-%s: display=full failed (%s), trying explicit fields...", ps_id, exc)

        # Strategy 2: LIST + filter + explicit fields (17 keys, no associations)
        product = {}
        try:
            data = self._api_get_long(
                'products', params={
                    'display': f'[{self._PS_PRODUCT_FIELDS}]',
                    'filter[id]': str(ps_id),
                },
                timeout=120,
            )
            product = self._extract_product_from_response(data)
            if product and len(product) > 2:
                _logger.info("PS-%s: fetched OK via explicit fields (%d keys, no associations)",
                             ps_id, len(product))
                return product
        except Exception as exc:
            _logger.warning("PS-%s: explicit fields also failed: %s", ps_id, exc)

        _logger.error("PS-%s: ALL fetch strategies failed. Keys: %s",
                       ps_id, list(product.keys()) if product else '(none)')
        return product if product else {}

    def action_sync_products(self):
        """Fetch all active products from PrestaShop and sync them into Odoo.

        Strategy: first fetch the lightweight list of IDs, then load each
        product individually to avoid a single massive API call that times out.
        """
        self.ensure_one()
        try:
            # Step 1 – get list of active product IDs (fast, lightweight)
            product_ids = self._fetch_product_ids()

            if not product_ids:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('No Products'),
                        'message': _('No active products found in PrestaShop.'),
                        'type': 'info',
                        'sticky': False,
                    },
                }

            created = updated = errors = 0
            total = len(product_ids)
            _logger.info("Starting product sync: %d products to process", total)

            # Step 2 – load each product one by one
            for idx, ps_id in enumerate(product_ids, 1):
                try:
                    already = self.env['product.template'].search([
                        ('prestashop_id', '=', ps_id),
                        ('prestashop_instance_id', '=', self.id),
                    ], limit=1)

                    ps_product = self._fetch_single_product_full(ps_id)
                    if ps_product:
                        self._sync_single_product(ps_product)
                        if already:
                            updated += 1
                        else:
                            created += 1

                    # commit after each product so we don't lose progress
                    if idx % 5 == 0:
                        self.env.cr.commit()  # noqa: B903
                        _logger.info("Product sync progress: %d/%d", idx, total)

                except Exception as exc:
                    errors += 1
                    _logger.error("Error syncing product PS-%s: %s", ps_id, exc)

            self.last_product_sync_date = fields.Datetime.now()
            self.env.cr.commit()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Product Sync Complete'),
                    'message': _(
                        'Created: %d | Updated: %d | Errors: %d (Total: %d)'
                    ) % (created, updated, errors, total),
                    'type': 'success' if not errors else 'warning',
                    'sticky': False,
                },
            }

        except Exception as exc:
            _logger.error("Product sync failed: %s", exc)
            raise UserError(_("Product sync failed: %s") % exc)

    def action_open_test_sync_wizard(self):
        """Open the test sync wizard pre-configured with 5 products."""
        self.ensure_one()
        wizard = self.env['prestashop.product.sync.wizard'].create({
            'instance_id': self.id,
            'product_limit': 5,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Test Product Sync'),
            'res_model': 'prestashop.product.sync.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_view_synced_products(self):
        """Open a list view of all products synced from this instance."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('PrestaShop Products'),
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [('prestashop_instance_id', '=', self.id)],
            'context': {'default_prestashop_instance_id': self.id},
        }

    def action_update_product_cron_interval(self):
        """Update the product sync cron interval."""
        self.ensure_one()
        cron = self.env.ref(
            'prestashop_product_sync.ir_cron_prestashop_product_sync',
            raise_if_not_found=False,
        )
        if cron:
            if self.product_sync_interval > 0:
                cron.write({
                    'interval_number': self.product_sync_interval,
                    'active': True,
                })
            else:
                cron.write({'active': False})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Cron Updated'),
                'message': (
                    _('Product auto-sync set to every %d minutes') % self.product_sync_interval
                    if self.product_sync_interval > 0
                    else _('Product auto-sync disabled')
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_clear_previews(self):
        """Delete all preview records for this instance."""
        self.ensure_one()
        self.env['prestashop.product.preview'].search([
            ('instance_id', '=', self.id),
        ]).unlink()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Previews Cleared'),
                'message': _('All preview records have been removed.'),
                'type': 'success',
                'sticky': False,
            },
        }

    # ------------------------------------
    # Cron
    # ------------------------------------
    @api.model
    def _cron_sync_products(self):
        """Cron entry-point: sync products for every active instance (hourly mode)."""
        for instance in self.search([
            ('active', '=', True),
            ('product_sync_mode', '=', 'hourly'),
        ]):
            try:
                instance.action_sync_products()
                _logger.info("Product cron sync OK for %s", instance.name)
            except Exception as exc:
                _logger.error("Product cron sync FAILED for %s: %s", instance.name, exc)

    @api.model
    def _cron_sync_products_daily(self):
        """Cron entry-point: daily product sync for instances in daily mode."""
        for instance in self.search([
            ('active', '=', True),
            ('product_sync_mode', '=', 'daily'),
        ]):
            try:
                instance.action_fetch_product_previews()
                instance.action_import_all_previews()
                _logger.info("Daily product sync OK for %s", instance.name)
            except Exception as exc:
                _logger.error("Daily product sync FAILED for %s: %s", instance.name, exc)

    # =============================================
    # EXPORT: API Write Methods
    # =============================================

    def _api_post(self, resource, xml_data, timeout=60):
        """Send POST request to PrestaShop API to create a resource.

        :param resource: API resource (e.g., 'products', 'combinations')
        :param xml_data: XML string body
        :param timeout: Request timeout in seconds
        :returns: dict with at least 'id' key from response
        """
        self.ensure_one()
        base_url = self._get_base_url()
        url = f"{base_url}/{resource}"
        headers = {'Content-Type': 'application/xml'}
        _logger.info("PS API POST: %s (body length: %d)", url, len(xml_data))
        _logger.debug("PS API POST body:\n%s", xml_data[:2000])
        resp = requests.post(
            url,
            auth=(self.api_key, ''),
            data=xml_data.encode('utf-8'),
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code not in (200, 201):
            _logger.error(
                "PS API POST error %s:\nURL: %s\nResponse: %s\nSent XML:\n%s",
                resp.status_code, url, resp.text[:500], xml_data[:1000],
            )
            raise UserError(
                _("PrestaShop API POST error (status %s).\nURL: %s\nResponse: %s")
                % (resp.status_code, url, resp.text[:500])
            )
        return self._parse_ps_xml_response(resp)

    def _api_put(self, resource, resource_id, xml_data, timeout=60):
        """Send PUT request to PrestaShop API to update a resource."""
        self.ensure_one()
        base_url = self._get_base_url()
        url = f"{base_url}/{resource}/{resource_id}"
        headers = {'Content-Type': 'application/xml'}
        _logger.info("PS API PUT: %s (body length: %d)", url, len(xml_data))
        resp = requests.put(
            url,
            auth=(self.api_key, ''),
            data=xml_data.encode('utf-8'),
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code != 200:
            _logger.error("PS API PUT error %s: %s", resp.status_code, resp.text[:500])
            raise UserError(
                _("PrestaShop API PUT error (status %s).\nURL: %s\nResponse: %s")
                % (resp.status_code, url, resp.text[:300])
            )
        return self._parse_ps_xml_response(resp)

    def _api_delete(self, resource, resource_id, timeout=30):
        """Delete a resource from PrestaShop."""
        self.ensure_one()
        base_url = self._get_base_url()
        url = f"{base_url}/{resource}/{resource_id}"
        _logger.info("PS API DELETE: %s", url)
        resp = requests.delete(
            url, auth=(self.api_key, ''), timeout=timeout,
        )
        if resp.status_code != 200:
            _logger.warning("PS API DELETE error %s: %s", resp.status_code, resp.text[:300])
        return resp.status_code == 200

    def _parse_ps_xml_response(self, response):
        """Parse PrestaShop XML response, extract resource ID."""
        result = {}
        try:
            content_type = response.headers.get('Content-Type', '')
            if 'json' in content_type:
                return response.json()
            root = ET.fromstring(response.content)
            for child in root:
                id_elem = child.find('id')
                if id_elem is not None and id_elem.text:
                    result['id'] = id_elem.text
                result['tag'] = child.tag
        except Exception as exc:
            _logger.warning("Failed to parse PS response: %s", exc)
            # Try to extract ID from text as fallback
            text = response.text or ''
            match = re.search(r'<id>\s*(\d+)\s*</id>', text)
            if match:
                result['id'] = match.group(1)
        return result

    # =============================================
    # EXPORT: XML Builders
    # =============================================

    @staticmethod
    def _slugify(text):
        """Generate URL-safe slug from text (for link_rewrite)."""
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
        text = re.sub(r'[^\w\s-]', '', text.lower())
        text = re.sub(r'[-\s]+', '-', text).strip('-')
        return text or 'product'

    # Module-level cache for PS language IDs per instance
    _PS_LANG_CACHE = {}

    def _get_ps_language_ids(self):
        """Fetch all active language IDs from PrestaShop.
        Falls back to export_default_ps_lang_id or [1] on error.
        Cached per instance ID (cleared via _clear_ps_lang_cache)."""
        self.ensure_one()
        cache_key = self.id
        if cache_key in self._PS_LANG_CACHE:
            return self._PS_LANG_CACHE[cache_key]
        try:
            data = self._api_get('languages', params={'display': '[id]', 'filter[active]': '1'})
            langs = data.get('languages', {}).get('language', [])
            if isinstance(langs, dict):
                langs = [langs]
            ids = [int(l['id']) for l in langs if l.get('id')]
            if ids:
                self._PS_LANG_CACHE[cache_key] = ids
                return ids
        except Exception:
            _logger.warning("Could not fetch PS languages, using default")
        default = self.export_default_ps_lang_id or 1
        return [default]

    def _clear_ps_lang_cache(self):
        """Clear the language cache for this instance."""
        self._PS_LANG_CACHE.pop(self.id, None)

    def _build_ps_language_xml(self, value, tag_name, lang_id=None):
        """Build multi-language XML element for PrestaShop.
        If lang_id is None, generates nodes for ALL active PS languages."""
        safe_value = saxutils.escape(str(value or ''))
        if lang_id is not None:
            lang_ids = [lang_id]
        else:
            lang_ids = self._get_ps_language_ids()
        inner = ''.join(
            '<language id="%s"><![CDATA[%s]]></language>' % (lid, safe_value)
            for lid in lang_ids
        )
        return '<%s>%s</%s>' % (tag_name, inner, tag_name)

    def _is_export_mapping_active(self, ps_field, odoo_field=None):
        """Check if a field mapping is active for export."""
        if not self.mapping_ids:
            return True
        domain = [
            ('instance_id', '=', self.id),
            ('ps_field_name', '=', ps_field),
        ]
        if odoo_field:
            domain.append(('odoo_field_name', '=', odoo_field))
        mapping = self.env['prestashop.field.mapping'].search(domain, limit=1)
        if not mapping:
            return True
        return mapping.export_active and mapping.direction in ('export', 'both')

    def _get_export_price(self, product_tmpl):
        """Get the price to export based on product's ps_export_price_type."""
        price_type = product_tmpl.ps_export_price_type or 'list_price'
        if price_type == 'standard_price':
            return product_tmpl.standard_price or 0.0
        if price_type == 'pricelist' and product_tmpl.ps_export_pricelist_id:
            pricelist = product_tmpl.ps_export_pricelist_id
            variant = product_tmpl.product_variant_id
            if variant:
                return pricelist._get_product_price(variant, 1.0)
        return product_tmpl.list_price or 0.0

    def _get_ps_blank_product(self):
        """Fetch the blank product schema from PrestaShop API.
        Used to discover required fields. Cached per instance."""
        self.ensure_one()
        cache_key = 'blank_%s' % self.id
        if cache_key in self._PS_LANG_CACHE:
            return self._PS_LANG_CACHE[cache_key]
        try:
            data = self._api_get('products', params={'schema': 'blank'})
            self._PS_LANG_CACHE[cache_key] = data
            return data
        except Exception:
            _logger.warning("Could not fetch PS blank product schema")
            return None

    def _build_product_xml(self, product_tmpl, ps_product_id=None):
        """Build XML payload for creating/updating a product in PrestaShop.

        Uses the PS blank schema approach: fetch the empty product XML from PS,
        then fill in the values. This ensures all required fields are present.

        :param product_tmpl: product.template record
        :param ps_product_id: if set, include <id> for PUT update
        :returns: XML string
        """
        self.ensure_one()
        # Clear cached language IDs for this build session
        self._clear_ps_lang_cache()

        parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        parts.append('<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">')
        parts.append('<product>')

        if ps_product_id:
            parts.append('<id>%s</id>' % ps_product_id)

        # --- REQUIRED PS FIELDS (always sent) ---
        # id_shop_default: required in PS 1.7+ (multistore)
        parts.append('<id_shop_default>1</id_shop_default>')

        # Product state: 0=draft, 1=active
        parts.append('<state>1</state>')

        # Product type: simple/pack/virtual/combinations
        if len(product_tmpl.product_variant_ids) > 1 and self.export_variants:
            parts.append('<type>combinations</type>')
        else:
            parts.append('<type>simple</type>')

        # name is mandatory
        parts.append(self._build_ps_language_xml(
            product_tmpl.name or 'Product', 'name'))

        # link_rewrite is mandatory
        slug = self._slugify(product_tmpl.name or 'product')
        parts.append(self._build_ps_language_xml(slug, 'link_rewrite'))

        # description (empty allowed, but node must exist for some PS versions)
        desc = ''
        if self._is_export_mapping_active('description'):
            desc = product_tmpl.prestashop_description_html or product_tmpl.description or ''
        parts.append(self._build_ps_language_xml(desc, 'description'))

        desc_short = ''
        if self._is_export_mapping_active('description_short'):
            desc_short = (
                product_tmpl.prestashop_description_short_html
                or product_tmpl.description_sale or ''
            )
        parts.append(self._build_ps_language_xml(desc_short, 'description_short'))

        # id_category_default is mandatory — fallback to PS Home (2)
        ps_cat_id = None
        if (self.export_categories
                and self._is_export_mapping_active('id_category_default')
                and product_tmpl.categ_id):
            try:
                ps_cat_id = self._get_or_create_ps_category(product_tmpl.categ_id)
            except Exception:
                _logger.warning("Category export failed, using default category 2")
        if not ps_cat_id:
            ps_cat_id = 2  # PS "Home" category
        parts.append('<id_category_default>%s</id_category_default>' % ps_cat_id)

        # id_tax_rules_group is required — fallback to 1
        tax_group = product_tmpl.prestashop_tax_rules_group_id or 1
        parts.append('<id_tax_rules_group>%s</id_tax_rules_group>' % tax_group)

        # --- PRICE ---
        price = self._get_export_price(product_tmpl)
        parts.append('<price>%.6f</price>' % price)

        if self._is_export_mapping_active('wholesale_price'):
            parts.append('<wholesale_price>%.6f</wholesale_price>' % (
                product_tmpl.standard_price or 0.0,
            ))

        # --- REFERENCE / EAN ---
        ref = product_tmpl.default_code or ''
        parts.append('<reference><![CDATA[%s]]></reference>' % ref)

        ean = product_tmpl.barcode or product_tmpl.prestashop_ean13 or ''
        # PS validates EAN13: must be empty or exactly 13 digits
        if ean and (len(ean) != 13 or not ean.isdigit()):
            ean = ''  # skip invalid EAN to avoid PS validation error
        parts.append('<ean13><![CDATA[%s]]></ean13>' % ean)

        # --- WEIGHT ---
        parts.append('<weight>%.6f</weight>' % (product_tmpl.weight or 0.0))

        # --- ACTIVE ---
        active_val = '1' if self.export_default_active else '0'
        parts.append('<active>%s</active>' % active_val)

        # --- VISIBILITY / AVAILABILITY ---
        parts.append('<available_for_order>1</available_for_order>')
        parts.append('<show_price>1</show_price>')
        parts.append('<visibility>both</visibility>')
        parts.append('<minimal_quantity>1</minimal_quantity>')

        # --- SEO ---
        if self._is_export_mapping_active('meta_title'):
            mt = product_tmpl.prestashop_meta_title or product_tmpl.name or ''
            parts.append(self._build_ps_language_xml(mt, 'meta_title'))

        if self._is_export_mapping_active('meta_description'):
            md = product_tmpl.prestashop_meta_description or ''
            parts.append(self._build_ps_language_xml(md, 'meta_description'))

        # --- ECO-TAX ---
        if self._is_export_mapping_active('ecotax'):
            parts.append('<ecotax>%.6f</ecotax>' % (product_tmpl.prestashop_ecotax or 0.0))

        # --- CATEGORY ASSOCIATIONS ---
        parts.append('<associations>')
        parts.append('<categories>')
        parts.append('<category><id>%s</id></category>' % ps_cat_id)
        parts.append('</categories>')
        parts.append('</associations>')

        parts.append('</product>')
        parts.append('</prestashop>')
        return '\n'.join(parts)

    # =============================================
    # EXPORT: Validation
    # =============================================

    def _validate_product_for_export(self, product_tmpl):
        """Validate a product before export. Returns list of error strings."""
        errors = []
        if not product_tmpl.name or not product_tmpl.name.strip():
            errors.append(_("Product name is empty."))
        price = self._get_export_price(product_tmpl)
        if price < 0:
            errors.append(_("Price cannot be negative: %s") % price)
        if product_tmpl.weight and product_tmpl.weight < 0:
            errors.append(_("Weight cannot be negative: %s") % product_tmpl.weight)
        ean = product_tmpl.barcode or product_tmpl.prestashop_ean13
        if ean and len(ean) not in (0, 8, 12, 13, 14):
            errors.append(
                _("Barcode '%s' has invalid length (%d). Must be 8, 12, 13, or 14 digits.")
                % (ean, len(ean))
            )
        if (product_tmpl.prestashop_instance_id
                and product_tmpl.prestashop_instance_id != self):
            errors.append(
                _("Product is already linked to instance '%s'. Cannot export to '%s'.")
                % (product_tmpl.prestashop_instance_id.name, self.name)
            )
        return errors

    # =============================================
    # EXPORT: Anti-Duplicate System (Triple Key)
    # =============================================

    def _find_ps_product_by_keys(self, product_tmpl):
        """Find an existing PrestaShop product using multi-key matching.

        Priority: 1) prestashop_id → 2) reference → 3) EAN13
        Returns: PS product ID (string) or None.
        """
        self.ensure_one()

        # Key 1: Direct PS ID
        if product_tmpl.prestashop_id:
            try:
                data = self._api_get_long('products', params={
                    'filter[id]': product_tmpl.prestashop_id,
                    'display': '[id]',
                }, timeout=15)
                products = data.get('products', [])
                if isinstance(products, dict):
                    products = [products]
                if products and str(products[0].get('id', '')) == product_tmpl.prestashop_id:
                    return product_tmpl.prestashop_id
            except Exception:
                pass

        # Key 2: Reference / SKU
        ref = product_tmpl.default_code
        if ref:
            try:
                data = self._api_get_long('products', params={
                    'filter[reference]': ref,
                    'display': '[id,reference]',
                }, timeout=15)
                products = data.get('products', [])
                if isinstance(products, dict):
                    products = [products]
                for p in products:
                    if str(p.get('reference', '')) == ref:
                        ps_id = str(p.get('id', ''))
                        _logger.info(
                            "Anti-dup: matched '%s' by reference '%s' -> PS-%s",
                            product_tmpl.name, ref, ps_id,
                        )
                        return ps_id
            except Exception as exc:
                _logger.warning("Anti-dup reference check failed: %s", exc)

        # Key 3: EAN13 / barcode
        ean = product_tmpl.barcode or product_tmpl.prestashop_ean13
        if ean and len(ean) in (8, 12, 13, 14):
            try:
                data = self._api_get_long('products', params={
                    'filter[ean13]': ean,
                    'display': '[id,ean13]',
                }, timeout=15)
                products = data.get('products', [])
                if isinstance(products, dict):
                    products = [products]
                for p in products:
                    if str(p.get('ean13', '')) == ean:
                        ps_id = str(p.get('id', ''))
                        _logger.info(
                            "Anti-dup: matched '%s' by EAN13 '%s' -> PS-%s",
                            product_tmpl.name, ean, ps_id,
                        )
                        return ps_id
            except Exception as exc:
                _logger.warning("Anti-dup EAN13 check failed: %s", exc)

        return None

    # =============================================
    # EXPORT: Single Product Export
    # =============================================

    def _export_single_product(self, product_tmpl, dry_run=False):
        """Export a single product to PrestaShop (create or update).

        :param product_tmpl: product.template record
        :param dry_run: if True, return XML without sending
        :returns: dict with keys: success, ps_id, operation, xml, error
        """
        self.ensure_one()
        start = time.time()
        result = {
            'success': False,
            'ps_id': None,
            'operation': None,
            'xml': None,
            'error': None,
        }

        try:
            # Validate
            errors = self._validate_product_for_export(product_tmpl)
            if errors:
                result['error'] = '\n'.join(errors)
                product_tmpl.write({
                    'ps_export_state': 'error',
                    'ps_export_error': result['error'],
                })
                return result

            # Anti-duplicate detection
            existing_ps_id = self._find_ps_product_by_keys(product_tmpl)

            if existing_ps_id:
                # UPDATE existing
                result['operation'] = 'update'
                xml = self._build_product_xml(product_tmpl, ps_product_id=existing_ps_id)
                result['xml'] = xml
                if dry_run:
                    result['success'] = True
                    result['ps_id'] = existing_ps_id
                    return result
                self._api_put('products', existing_ps_id, xml)
                result['ps_id'] = existing_ps_id
                product_tmpl.write({
                    'prestashop_id': existing_ps_id,
                    'prestashop_instance_id': self.id,
                    'ps_last_export': fields.Datetime.now(),
                    'ps_export_state': 'exported',
                    'ps_export_error': False,
                })
            else:
                # CREATE new
                result['operation'] = 'create'
                xml = self._build_product_xml(product_tmpl)
                result['xml'] = xml
                if dry_run:
                    result['success'] = True
                    return result
                resp = self._api_post('products', xml)
                new_ps_id = resp.get('id') or ''
                result['ps_id'] = new_ps_id
                product_tmpl.write({
                    'prestashop_id': new_ps_id,
                    'prestashop_instance_id': self.id,
                    'ps_last_export': fields.Datetime.now(),
                    'ps_export_state': 'exported',
                    'ps_export_error': False,
                })

            # Handle variants
            if (self.export_variants
                    and result['ps_id']
                    and len(product_tmpl.product_variant_ids) > 1):
                self._export_product_variants(product_tmpl, result['ps_id'])

            # Handle images
            if self.export_images and product_tmpl.image_1920 and result['ps_id']:
                self._export_product_images(product_tmpl, result['ps_id'])

            result['success'] = True

            # Log
            duration = int((time.time() - start) * 1000)
            self.env['prestashop.export.log'].create({
                'instance_id': self.id,
                'product_tmpl_id': product_tmpl.id,
                'operation': result['operation'],
                'ps_product_id': result['ps_id'],
                'success': True,
                'request_xml': xml,
                'duration_ms': duration,
            })

        except Exception as exc:
            result['error'] = str(exc)
            product_tmpl.write({
                'ps_export_state': 'error',
                'ps_export_error': str(exc)[:500],
            })
            self.env['prestashop.export.log'].create({
                'instance_id': self.id,
                'product_tmpl_id': product_tmpl.id,
                'operation': 'error',
                'ps_product_id': result.get('ps_id') or product_tmpl.prestashop_id or '',
                'success': False,
                'error_message': str(exc),
                'request_xml': result.get('xml') or '',
            })
            _logger.error("Export failed for product %s: %s", product_tmpl.name, exc)

        return result

    # =============================================
    # EXPORT: Batch Export (Background Thread)
    # =============================================

    def _export_products_background(self, product_ids):
        """Run product export in a background daemon thread.

        Mirrors the existing _import_previews_background() pattern.
        """
        self.ensure_one()
        db_name = self.env.cr.dbname
        uid = self.env.uid
        instance_id = self.id

        self.export_running = True
        self.env.cr.commit()

        def _run():
            try:
                db_registry = odoo.registry(db_name)
                with db_registry.cursor() as cr:
                    env = odoo.api.Environment(cr, uid, {})
                    instance = env['prestashop.instance'].browse(instance_id)
                    products = env['product.template'].browse(product_ids).exists()

                    total = len(products)
                    created = updated = errors = 0

                    for idx, product in enumerate(products, 1):
                        try:
                            env['bus.bus']._sendone(
                                env.user.partner_id,
                                'simple_notification',
                                {
                                    'title': 'Export %d/%d' % (idx, total),
                                    'message': product.name or '',
                                    'type': 'info',
                                    'sticky': False,
                                },
                            )
                            cr.commit()

                            result = instance._export_single_product(product)

                            if result['success']:
                                if result['operation'] == 'create':
                                    created += 1
                                else:
                                    updated += 1
                                env['bus.bus']._sendone(
                                    env.user.partner_id,
                                    'simple_notification',
                                    {
                                        'title': '%s %d/%d' % (
                                            'Created' if result['operation'] == 'create' else 'Updated',
                                            idx, total,
                                        ),
                                        'message': product.name,
                                        'type': 'success',
                                        'sticky': False,
                                    },
                                )
                            else:
                                errors += 1

                            cr.commit()

                        except Exception as exc:
                            errors += 1
                            _logger.error("BG export error %s: %s", product.name, exc)
                            cr.rollback()

                    instance.write({
                        'last_product_export_date': fields.Datetime.now(),
                        'export_running': False,
                    })
                    cr.commit()

                    env['bus.bus']._sendone(
                        env.user.partner_id,
                        'simple_notification',
                        {
                            'title': 'Export Complete!',
                            'message': 'Created: %d | Updated: %d | Errors: %d' % (
                                created, updated, errors,
                            ),
                            'type': 'success' if not errors else 'warning',
                            'sticky': True,
                        },
                    )
                    cr.commit()

            except Exception as exc:
                _logger.error("Background export thread failed: %s", exc)
                try:
                    db_registry = odoo.registry(db_name)
                    with db_registry.cursor() as cr:
                        env = odoo.api.Environment(cr, uid, {})
                        env['prestashop.instance'].browse(instance_id).export_running = False
                        cr.commit()
                except Exception:
                    pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    # =============================================
    # EXPORT: Variant / Combination Support
    # =============================================

    def _export_product_variants(self, product_tmpl, ps_product_id):
        """Export Odoo variants as PrestaShop combinations."""
        existing_map = {}
        if product_tmpl.ps_combination_ids_json:
            try:
                existing_map = json.loads(product_tmpl.ps_combination_ids_json)
            except (ValueError, TypeError):
                pass

        variants = product_tmpl.product_variant_ids
        if len(variants) <= 1:
            return

        for variant in variants:
            variant_id_str = str(variant.id)
            ps_combo_id = existing_map.get(variant_id_str)

            xml = self._build_combination_xml(variant, ps_product_id, ps_combo_id)

            try:
                if ps_combo_id:
                    self._api_put('combinations', ps_combo_id, xml)
                    op = 'variant_update'
                else:
                    resp = self._api_post('combinations', xml)
                    new_combo_id = resp.get('id', '')
                    existing_map[variant_id_str] = new_combo_id
                    ps_combo_id = new_combo_id
                    op = 'variant_create'

                self.env['prestashop.export.log'].create({
                    'instance_id': self.id,
                    'product_tmpl_id': product_tmpl.id,
                    'product_product_id': variant.id,
                    'operation': op,
                    'ps_product_id': ps_product_id,
                    'ps_combination_id': ps_combo_id,
                    'success': True,
                })
            except Exception as exc:
                _logger.error(
                    "Variant export failed for %s (variant %s): %s",
                    product_tmpl.name, variant.display_name, exc,
                )

        product_tmpl.ps_combination_ids_json = json.dumps(existing_map)

    def _build_combination_xml(self, variant, ps_product_id, ps_combo_id=None):
        """Build XML for a PrestaShop combination."""
        parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        parts.append('<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">')
        parts.append('<combination>')

        if ps_combo_id:
            parts.append('<id>%s</id>' % ps_combo_id)

        parts.append('<id_product>%s</id_product>' % ps_product_id)

        # Price impact (variant - template)
        price_impact = (variant.lst_price or 0.0) - (variant.product_tmpl_id.list_price or 0.0)
        parts.append('<price>%.6f</price>' % price_impact)

        # Weight impact
        weight_impact = (variant.weight or 0.0) - (variant.product_tmpl_id.weight or 0.0)
        parts.append('<weight>%.6f</weight>' % weight_impact)

        ref = saxutils.escape(variant.default_code or '')
        parts.append('<reference><![CDATA[%s]]></reference>' % ref)

        ean = variant.barcode or ''
        parts.append('<ean13>%s</ean13>' % saxutils.escape(ean))

        # Minimal quantity
        parts.append('<minimal_quantity>1</minimal_quantity>')

        # Attribute values (associations)
        attr_values = variant.product_template_attribute_value_ids
        if attr_values:
            parts.append('<associations>')
            parts.append('<product_option_values>')
            for ptav in attr_values:
                ps_option_value_id = self._ensure_ps_option_value(ptav)
                if ps_option_value_id:
                    parts.append(
                        '<product_option_value><id>%s</id></product_option_value>'
                        % ps_option_value_id
                    )
            parts.append('</product_option_values>')
            parts.append('</associations>')

        parts.append('</combination>')
        parts.append('</prestashop>')
        return '\n'.join(parts)

    def _ensure_ps_option_value(self, ptav):
        """Ensure a product.template.attribute.value exists in PrestaShop.

        Returns PS product_option_value ID or None.
        """
        attr_value = ptav.product_attribute_value_id
        attribute = attr_value.attribute_id

        ps_option_id = self._find_or_create_ps_option(attribute)
        if not ps_option_id:
            return None

        ps_option_value_id = self._find_or_create_ps_option_value(
            attr_value, ps_option_id,
        )
        return ps_option_value_id

    def _find_or_create_ps_option(self, attribute):
        """Find or create a product_option (attribute) in PrestaShop."""
        name = attribute.name
        try:
            data = self._api_get_long('product_options', params={
                'filter[name]': name,
                'display': '[id,name]',
            }, timeout=15)
            options = data.get('product_options', [])
            if isinstance(options, dict):
                options = [options]
            for opt in options:
                opt_name = self._get_ps_text(opt.get('name', ''))
                if opt_name == name:
                    return str(opt.get('id', ''))
        except Exception:
            pass

        # Create new
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">\n'
            '<product_option>\n'
            '%s\n'
            '%s\n'
            '<group_type>select</group_type>\n'
            '</product_option>\n'
            '</prestashop>'
        ) % (
            self._build_ps_language_xml(name, 'name'),
            self._build_ps_language_xml(name, 'public_name'),
        )
        try:
            resp = self._api_post('product_options', xml)
            return resp.get('id', '')
        except Exception as exc:
            _logger.error("Failed to create PS option '%s': %s", name, exc)
            return None

    def _find_or_create_ps_option_value(self, attr_value, ps_option_id):
        """Find or create a product_option_value in PrestaShop."""
        name = attr_value.name
        try:
            data = self._api_get_long('product_option_values', params={
                'filter[id_attribute_group]': ps_option_id,
                'display': '[id,name]',
            }, timeout=15)
            values = data.get('product_option_values', [])
            if isinstance(values, dict):
                values = [values]
            for val in values:
                val_name = self._get_ps_text(val.get('name', ''))
                if val_name == name:
                    return str(val.get('id', ''))
        except Exception:
            pass

        # Create new
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">\n'
            '<product_option_value>\n'
            '<id_attribute_group>%s</id_attribute_group>\n'
            '%s\n'
            '</product_option_value>\n'
            '</prestashop>'
        ) % (
            ps_option_id,
            self._build_ps_language_xml(name, 'name'),
        )
        try:
            resp = self._api_post('product_option_values', xml)
            return resp.get('id', '')
        except Exception as exc:
            _logger.error("Failed to create PS option value '%s': %s", name, exc)
            return None

    # =============================================
    # EXPORT: Stock Sync
    # =============================================

    def _compute_ps_stock_qty(self, product_tmpl, variant=None):
        """Compute stock quantity to push to PrestaShop.

        Uses per-product stock location if set, else instance warehouse.
        """
        product = variant or product_tmpl.product_variant_id
        if not product:
            return 0

        location = product_tmpl.ps_stock_location_id
        if not location:
            warehouse = self.warehouse_id
            location = warehouse.lot_stock_id if warehouse else None

        if not location:
            return 0

        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'child_of', location.id),
        ])
        qty = sum(q.quantity - q.reserved_quantity for q in quants)
        return max(int(qty), 0)

    def _push_stock_to_ps(self, product_tmpl):
        """Push stock quantity for a product (and its variants) to PrestaShop."""
        self.ensure_one()
        ps_id = product_tmpl.prestashop_id
        if not ps_id:
            return

        variants = product_tmpl.product_variant_ids
        combo_map = {}
        if product_tmpl.ps_combination_ids_json:
            try:
                combo_map = json.loads(product_tmpl.ps_combination_ids_json)
            except (ValueError, TypeError):
                pass

        if len(variants) <= 1 or not combo_map:
            qty = self._compute_ps_stock_qty(product_tmpl)
            self._update_ps_stock_available(ps_id, '0', qty, product_tmpl)
        else:
            for variant in variants:
                ps_combo_id = combo_map.get(str(variant.id))
                if ps_combo_id:
                    qty = self._compute_ps_stock_qty(product_tmpl, variant)
                    self._update_ps_stock_available(ps_id, ps_combo_id, qty, product_tmpl)

    def _update_ps_stock_available(self, ps_product_id, ps_attribute_id, quantity,
                                   product_tmpl=None):
        """Update a stock_available record in PrestaShop."""
        # Find the stock_available ID
        try:
            data = self._api_get_long(
                'stock_availables', params={
                    'filter[id_product]': str(ps_product_id),
                    'filter[id_product_attribute]': str(ps_attribute_id),
                    'display': '[id,quantity]',
                }, timeout=30,
            )
        except Exception as exc:
            _logger.warning("Stock available lookup failed for PS-%s: %s", ps_product_id, exc)
            return

        stocks = data.get('stock_availables', [])
        if isinstance(stocks, dict):
            stocks = [stocks]

        if not stocks:
            _logger.warning(
                "No stock_available found for PS product %s, attribute %s",
                ps_product_id, ps_attribute_id,
            )
            return

        stock_id = stocks[0].get('id')
        if not stock_id:
            return

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">\n'
            '<stock_available>\n'
            '<id>%s</id>\n'
            '<id_product>%s</id_product>\n'
            '<id_product_attribute>%s</id_product_attribute>\n'
            '<quantity>%d</quantity>\n'
            '</stock_available>\n'
            '</prestashop>'
        ) % (stock_id, ps_product_id, ps_attribute_id, int(quantity))

        self._api_put('stock_availables', stock_id, xml)

        self.env['prestashop.export.log'].create({
            'instance_id': self.id,
            'product_tmpl_id': product_tmpl.id if product_tmpl else False,
            'operation': 'stock',
            'ps_product_id': ps_product_id,
            'ps_combination_id': ps_attribute_id if ps_attribute_id != '0' else False,
            'success': True,
            'request_xml': xml,
        })

        _logger.info(
            "Stock updated in PS: product %s, attribute %s, qty %d",
            ps_product_id, ps_attribute_id, quantity,
        )

    # =============================================
    # EXPORT: Price Sync
    # =============================================

    def _push_price_to_ps(self, product_tmpl):
        """Push price update to PrestaShop for a single product."""
        self.ensure_one()
        ps_id = product_tmpl.prestashop_id
        if not ps_id:
            return

        price = self._get_export_price(product_tmpl)
        # PS requires name and link_rewrite for PUT even when only updating price
        self._clear_ps_lang_cache()
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">\n'
            '<product>\n'
            '<id>%s</id>\n'
            '<price>%.6f</price>\n'
            '<wholesale_price>%.6f</wholesale_price>\n'
            '%s\n'
            '%s\n'
            '</product>\n'
            '</prestashop>'
        ) % (
            ps_id,
            price,
            product_tmpl.standard_price or 0.0,
            self._build_ps_language_xml(product_tmpl.name or '', 'name'),
            self._build_ps_language_xml(
                self._slugify(product_tmpl.name or 'product'), 'link_rewrite',
            ),
        )

        self._api_put('products', ps_id, xml)

        self.env['prestashop.export.log'].create({
            'instance_id': self.id,
            'product_tmpl_id': product_tmpl.id,
            'operation': 'price',
            'ps_product_id': ps_id,
            'success': True,
            'request_xml': xml,
            'field_changes': json.dumps({'price': price}),
        })

    # =============================================
    # EXPORT: Category Export
    # =============================================

    def _get_or_create_ps_category(self, odoo_category):
        """Ensure an Odoo product.category exists in PrestaShop.

        Returns PS category ID string, or None.
        """
        if not odoo_category:
            return None

        # Already mapped?
        if odoo_category.prestashop_id and odoo_category.prestashop_instance_id == self:
            return odoo_category.prestashop_id

        # Search PS by name
        name = odoo_category.name
        try:
            data = self._api_get_long('categories', params={
                'filter[name]': name,
                'display': '[id,name]',
            }, timeout=15)
            cats = data.get('categories', [])
            if isinstance(cats, dict):
                cats = [cats]
            for cat in cats:
                cat_name = self._get_ps_text(cat.get('name', ''))
                if cat_name == name:
                    ps_cat_id = str(cat.get('id', ''))
                    odoo_category.write({
                        'prestashop_id': ps_cat_id,
                        'prestashop_instance_id': self.id,
                    })
                    return ps_cat_id
        except Exception:
            pass

        # Create new category in PS
        parent_ps_id = '2'  # PS "Home" category
        if odoo_category.parent_id:
            parent_ps_id = self._get_or_create_ps_category(odoo_category.parent_id) or '2'

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">\n'
            '<category>\n'
            '%s\n'
            '<id_parent>%s</id_parent>\n'
            '<active>1</active>\n'
            '%s\n'
            '</category>\n'
            '</prestashop>'
        ) % (
            self._build_ps_language_xml(name, 'name'),
            parent_ps_id,
            self._build_ps_language_xml(self._slugify(name), 'link_rewrite'),
        )

        try:
            resp = self._api_post('categories', xml)
            new_id = resp.get('id', '')
            odoo_category.write({
                'prestashop_id': new_id,
                'prestashop_instance_id': self.id,
            })
            self.env['prestashop.export.log'].create({
                'instance_id': self.id,
                'operation': 'category',
                'ps_product_id': new_id,
                'success': True,
                'request_xml': xml,
            })
            return new_id
        except Exception as exc:
            _logger.error("Failed to create PS category '%s': %s", name, exc)
            return None

    # =============================================
    # EXPORT: Image Export
    # =============================================

    def _export_product_images(self, product_tmpl, ps_product_id):
        """Export product images to PrestaShop via multipart upload."""
        if not product_tmpl.image_1920:
            return

        base_url = self._get_base_url()
        url = f"{base_url}/images/products/{ps_product_id}"

        image_data = base64.b64decode(product_tmpl.image_1920)

        files = {
            'image': ('product.jpg', image_data, 'image/jpeg'),
        }

        try:
            resp = requests.post(
                url,
                auth=(self.api_key, ''),
                files=files,
                timeout=60,
            )
            if resp.status_code in (200, 201):
                _logger.info("Image exported for PS product %s", ps_product_id)
                self.env['prestashop.export.log'].create({
                    'instance_id': self.id,
                    'product_tmpl_id': product_tmpl.id,
                    'operation': 'image',
                    'ps_product_id': ps_product_id,
                    'success': True,
                })
            else:
                _logger.warning(
                    "Image export failed for PS-%s: status %s",
                    ps_product_id, resp.status_code,
                )
        except Exception as exc:
            _logger.error("Image export error for PS-%s: %s", ps_product_id, exc)

    # =============================================
    # EXPORT: Conflict Detection
    # =============================================

    def _detect_export_conflicts(self, product_tmpl):
        """Detect if a product was modified on both Odoo and PrestaShop.

        Returns: 'no_conflict', 'ps_newer', 'odoo_newer', 'both_modified'
        """
        if not product_tmpl.prestashop_id:
            return 'no_conflict'
        try:
            ps_data = self._fetch_single_product_full(product_tmpl.prestashop_id)
            ps_date_upd = ps_data.get('date_upd', '')
            if ps_date_upd:
                from datetime import datetime as dt
                ps_modified = dt.strptime(ps_date_upd, '%Y-%m-%d %H:%M:%S')
                last_export = product_tmpl.ps_last_export
                if last_export and ps_modified > last_export.replace(tzinfo=None):
                    if product_tmpl.ps_export_state == 'modified':
                        return 'both_modified'
                    return 'ps_newer'
                if product_tmpl.ps_export_state == 'modified':
                    return 'odoo_newer'
        except Exception:
            pass
        return 'no_conflict'

    # =============================================
    # EXPORT: Cron Jobs
    # =============================================

    @api.model
    def _cron_export_products(self):
        """Cron: export new/modified products for instances with export enabled."""
        for instance in self.search([
            ('active', '=', True),
            ('export_sync_mode', '!=', 'disabled'),
        ]):
            products = self.env['product.template'].search([
                ('ps_export_enabled', '=', True),
                ('ps_export_state', 'in', ('not_exported', 'modified')),
                '|',
                ('prestashop_instance_id', '=', instance.id),
                ('prestashop_instance_id', '=', False),
            ])
            if products:
                instance._export_products_background(products.ids)

    @api.model
    def _cron_push_stock(self):
        """Cron: push stock for all export-enabled products."""
        for instance in self.search([
            ('active', '=', True),
            ('stock_sync_mode', '!=', 'disabled'),
        ]):
            products = self.env['product.template'].search([
                ('prestashop_instance_id', '=', instance.id),
                ('ps_export_enabled', '=', True),
                ('prestashop_id', '!=', False),
            ])
            for product in products:
                try:
                    instance._push_stock_to_ps(product)
                except Exception as exc:
                    _logger.error("Stock push failed for %s: %s", product.name, exc)
            instance.last_stock_export_date = fields.Datetime.now()

    @api.model
    def _cron_push_prices(self):
        """Cron: push prices for export-enabled products."""
        for instance in self.search([
            ('active', '=', True),
            ('price_sync_mode', '!=', 'disabled'),
        ]):
            products = self.env['product.template'].search([
                ('prestashop_instance_id', '=', instance.id),
                ('ps_export_enabled', '=', True),
                ('prestashop_id', '!=', False),
            ])
            for product in products:
                try:
                    instance._push_price_to_ps(product)
                except Exception as exc:
                    _logger.error("Price push failed for %s: %s", product.name, exc)
            instance.last_price_export_date = fields.Datetime.now()

    @api.model
    def _cron_process_export_queue(self):
        """Process pending items in the export queue with retry logic."""
        queued = self.env['prestashop.export.queue'].search([
            ('state', '=', 'pending'),
            ('retry_count', '<', 3),
            '|',
            ('scheduled_date', '=', False),
            ('scheduled_date', '<=', fields.Datetime.now()),
        ], limit=50, order='priority desc, create_date asc')

        for item in queued:
            try:
                item.state = 'processing'
                item.env.cr.commit()

                instance = item.instance_id
                product = item.product_tmpl_id

                if item.operation in ('create', 'update'):
                    result = instance._export_single_product(product)
                elif item.operation == 'price':
                    instance._push_price_to_ps(product)
                    result = {'success': True}
                elif item.operation == 'stock':
                    instance._push_stock_to_ps(product)
                    result = {'success': True}
                elif item.operation == 'variant':
                    if product.prestashop_id:
                        instance._export_product_variants(product, product.prestashop_id)
                    result = {'success': True}
                else:
                    result = {'success': False, 'error': 'Unknown operation'}

                if result.get('success'):
                    item.write({
                        'state': 'done',
                        'executed_date': fields.Datetime.now(),
                        'result_ps_id': result.get('ps_id', ''),
                    })
                else:
                    item.write({
                        'state': 'error',
                        'error_message': result.get('error', 'Unknown error'),
                        'retry_count': item.retry_count + 1,
                    })

                item.env.cr.commit()

            except Exception as exc:
                item.write({
                    'state': 'error' if item.retry_count >= 2 else 'pending',
                    'error_message': str(exc)[:500],
                    'retry_count': item.retry_count + 1,
                })
                item.env.cr.commit()
