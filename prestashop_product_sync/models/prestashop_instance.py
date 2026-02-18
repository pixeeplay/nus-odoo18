import base64
import logging
import threading

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
            data = self._api_get('categories', cat_str)
            cat = data.get('category', {})
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
            data = self._api_get('manufacturers', str(manufacturer_id))
            return data.get('manufacturer', {}).get('name', '') or ''
        except Exception:
            return ''

    # ------------------------------------
    # Helpers – tax resolution
    # ------------------------------------
    def _resolve_tax_rate(self, tax_rules_group_id):
        """Resolve a PrestaShop tax_rules_group ID to (rate, group_name).

        Chain: tax_rule_groups → tax_rules → taxes
        Returns (float rate, str group_name).
        """
        self.ensure_one()
        group_name = ''
        rate = 0.0
        try:
            # Get the tax rules group name
            grp_data = self._api_get_long(
                'tax_rule_groups', resource_id=str(tax_rules_group_id),
                timeout=30,
            )
            grp = grp_data.get('tax_rule_group', {})
            group_name = self._get_ps_text(grp.get('name', ''))

            # Get tax rules for this group
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
                    tax_data = self._api_get_long(
                        'taxes', resource_id=tax_id_ps, timeout=30,
                    )
                    tax = tax_data.get('tax', {})
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
            data = self._api_get('product_features', str(feature_id))
            return self._get_ps_text(data.get('product_feature', {}).get('name', ''))
        except Exception:
            return f'Feature {feature_id}'

    def _get_feature_value_text(self, value_id):
        try:
            data = self._api_get('product_feature_values', str(value_id))
            return self._get_ps_text(data.get('product_feature_value', {}).get('value', ''))
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
            image_data = associations.get('images', {}) or {}
            image_list = image_data.get('image', [])
            if isinstance(image_list, dict):
                image_list = [image_list]
            _logger.info(
                "PS-%s: %d images to sync (raw images key: %s)",
                ps_id, len(image_list),
                type(associations.get('images')).__name__,
            )
            self._sync_product_images_to_odoo(product_tmpl, ps_id, image_list)

        # --- features / characteristics (respects mapping) ---
        if (self.sync_product_features
                and self._is_mapping_active('associations.product_features')):
            feat_data = associations.get('product_features', {}) or {}
            feat_list = feat_data.get('product_feature', [])
            if isinstance(feat_list, dict):
                feat_list = [feat_list]
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

    def _fetch_single_product_full(self, ps_product_id):
        """Fetch full details for a single product by ID."""
        self.ensure_one()
        data = self._api_get_long(
            'products', resource_id=str(ps_product_id),
            params={'display': 'full'},
            timeout=120,
        )
        # PrestaShop may return {'product': {...}} or {'products': [{...}]}
        product = data.get('product')
        if not product:
            products = data.get('products', [])
            if isinstance(products, list) and products:
                product = products[0]
            elif isinstance(products, dict):
                product = products
        if not product:
            _logger.warning(
                "Empty product response for PS-%s. Keys: %s",
                ps_product_id, list(data.keys()),
            )
        return product or {}

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
