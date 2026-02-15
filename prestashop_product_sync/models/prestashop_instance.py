import base64
import logging

import requests

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
    default_product_categ_id = fields.Many2one(
        'product.category', 'Default Product Category',
    )
    product_ids = fields.One2many(
        'product.template', 'prestashop_instance_id', string='Synced Products',
    )
    product_count = fields.Integer('Product Count', compute='_compute_product_count')
    product_sync_interval = fields.Integer(
        'Product Sync Interval (min)', default=60,
        help="Auto-sync interval for products in minutes. 0 = disabled.",
    )

    @api.depends('product_ids')
    def _compute_product_count(self):
        for rec in self:
            rec.product_count = len(rec.product_ids)

    # ------------------------------------
    # Helpers – multi-language text
    # ------------------------------------
    def _get_ps_text(self, field_data, lang_id=1):
        """Extract text value from a PrestaShop multi-language field.

        PrestaShop may return:
        - a plain string
        - a list like [{"id": "1", "value": "text"}, ...]
        - a dict  {"language": [{"attrs": {"id": "1"}, "value": "text"}, ...]}
        """
        if not field_data:
            return ''
        if isinstance(field_data, str):
            return field_data
        if isinstance(field_data, list):
            for item in field_data:
                if isinstance(item, dict) and str(item.get('id', '')) == str(lang_id):
                    return item.get('value', '') or ''
            # fallback: first item
            if field_data and isinstance(field_data[0], dict):
                return field_data[0].get('value', '') or ''
        if isinstance(field_data, dict):
            langs = field_data.get('language', [])
            if isinstance(langs, list):
                for item in langs:
                    if isinstance(item, dict):
                        attrs = item.get('attrs', {})
                        if str(attrs.get('id', '')) == str(lang_id):
                            return item.get('value', '') or ''
                if langs and isinstance(langs[0], dict):
                    return langs[0].get('value', '') or ''
            return field_data.get('value', '') or ''
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
    # Core – sync a single product
    # ------------------------------------
    def _sync_single_product(self, ps_product):
        """Import / update a single PrestaShop product into Odoo."""
        self.ensure_one()
        ps_id = str(ps_product.get('id', ''))
        if not ps_id:
            return None

        # --- text fields ---
        name = self._get_ps_text(ps_product.get('name', ''))
        description = self._get_ps_text(ps_product.get('description', ''))
        description_short = self._get_ps_text(ps_product.get('description_short', ''))
        reference = ps_product.get('reference', '') or ''
        ean13 = ps_product.get('ean13', '') or ''
        price = float(ps_product.get('price', 0) or 0)
        wholesale_price = float(ps_product.get('wholesale_price', 0) or 0)
        weight = float(ps_product.get('weight', 0) or 0)
        id_category_default = str(ps_product.get('id_category_default', '0'))
        id_manufacturer = str(ps_product.get('id_manufacturer', '0'))
        meta_title = self._get_ps_text(ps_product.get('meta_title', ''))
        meta_description = self._get_ps_text(ps_product.get('meta_description', ''))
        link_rewrite = self._get_ps_text(ps_product.get('link_rewrite', ''))
        associations = ps_product.get('associations', {}) or {}

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

        # --- category ---
        categ_id = False
        if self.sync_product_categories and id_category_default:
            categ = self._get_or_create_category(id_category_default)
            if categ:
                categ_id = categ.id

        # --- manufacturer ---
        manufacturer = self._get_manufacturer_name(id_manufacturer)

        # --- prepare vals ---
        vals = {
            'name': name or f'Product PS-{ps_id}',
            'default_code': reference or False,
            'list_price': price,
            'standard_price': wholesale_price,
            'weight': weight,
            'description': description or False,
            'description_sale': description_short or False,
            'prestashop_id': ps_id,
            'prestashop_instance_id': self.id,
            'prestashop_url': product_url or False,
            'prestashop_last_sync': fields.Datetime.now(),
            'prestashop_description_html': description or False,
            'prestashop_description_short_html': description_short or False,
            'prestashop_meta_title': meta_title or False,
            'prestashop_meta_description': meta_description or False,
            'prestashop_manufacturer': manufacturer or False,
            'prestashop_ean13': ean13 or False,
            'type': 'consu',
        }

        # barcode – only set if valid length
        if ean13 and len(ean13) in (8, 12, 13, 14):
            # avoid duplicate barcode errors
            dup = self.env['product.template'].search([
                ('barcode', '=', ean13),
                ('id', '!=', product_tmpl.id if product_tmpl else 0),
            ], limit=1)
            if not dup:
                vals['barcode'] = ean13

        if categ_id:
            vals['categ_id'] = categ_id

        # --- create or update ---
        if product_tmpl:
            product_tmpl.write(vals)
            _logger.info("Updated product '%s' (PS ID %s)", name, ps_id)
        else:
            product_tmpl = self.env['product.template'].create(vals)
            _logger.info("Created product '%s' (PS ID %s)", name, ps_id)

        # --- images ---
        if self.sync_product_images:
            image_data = associations.get('images', {}) or {}
            image_list = image_data.get('image', [])
            if isinstance(image_list, dict):
                image_list = [image_list]
            self._sync_product_images_to_odoo(product_tmpl, ps_id, image_list)

        # --- features / characteristics ---
        if self.sync_product_features:
            feat_data = associations.get('product_features', {}) or {}
            feat_list = feat_data.get('product_feature', [])
            if isinstance(feat_list, dict):
                feat_list = [feat_list]
            self._sync_product_features_to_odoo(product_tmpl, feat_list)

        return product_tmpl

    # ------------------------------------
    # Actions
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
        return data.get('product', {})

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

    # ------------------------------------
    # Cron
    # ------------------------------------
    @api.model
    def _cron_sync_products(self):
        """Cron entry-point: sync products for every active instance."""
        for instance in self.search([('active', '=', True)]):
            try:
                instance.action_sync_products()
                _logger.info("Product cron sync OK for %s", instance.name)
            except Exception as exc:
                _logger.error("Product cron sync FAILED for %s: %s", instance.name, exc)
