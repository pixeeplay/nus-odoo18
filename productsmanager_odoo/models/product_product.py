import base64
import json
import logging

import requests as http_requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .pm_api import ProductsManagerAPIError

_logger = logging.getLogger(__name__)


def _safe_float(val):
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val):
    try:
        return int(float(val)) if val else 0
    except (ValueError, TypeError):
        return 0


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def init(self):
        """Runs on every server start — create missing columns.

        theme_nova registers nova_label_id on product.template in Python,
        but if the DB column doesn't exist, ANY read of product.template
        crashes the entire application. This ensures the column exists.
        """
        self.env.cr.execute(
            "ALTER TABLE product_template "
            "ADD COLUMN IF NOT EXISTS nova_label_id integer"
        )

    # ── Existing PM fields ─────────────────────────────────────────────
    pm_external_id = fields.Char(string='PM External ID', index=True, copy=False)
    pm_last_sync = fields.Datetime(string='PM Last Sync', readonly=True, copy=False)
    pm_brand = fields.Char(string='PM Brand')
    pm_completeness = fields.Integer(string='PM Completeness (%)', default=0)
    pm_supplier_prices_html = fields.Html(
        string='Supplier Prices',
        compute='_compute_pm_supplier_prices_html',
        sanitize=False,
    )

    # ── PM Full Detail Fields ──────────────────────────────────────────
    # Identity
    pm_ean = fields.Char(string='EAN')
    pm_asin = fields.Char(string='ASIN')
    pm_upc = fields.Char(string='UPC')
    pm_gtin = fields.Char(string='GTIN')
    pm_manufacturer_ref = fields.Char(string='Manufacturer Ref')
    pm_manufacturer = fields.Char(string='Manufacturer')

    # Pricing
    pm_cost_price = fields.Float(string='PM Cost Price', digits='Product Price')
    pm_recommended_price = fields.Float(string='Recommended Price', digits='Product Price')
    pm_margin = fields.Float(string='Margin (%)')

    # Competitor Prices
    pm_competitor_prices_json = fields.Text(string='Competitor Prices JSON')
    pm_competitor_prices_html = fields.Html(
        string='Competitor Prices',
        compute='_compute_pm_competitor_prices_html',
        sanitize=False,
    )

    # Stock / Availability
    pm_stock_quantity = fields.Integer(string='PM Stock Qty')
    pm_is_low_stock = fields.Boolean(string='Low Stock')
    pm_min_order_qty = fields.Integer(string='Min Order Qty')
    pm_max_order_qty = fields.Integer(string='Max Order Qty')
    pm_lead_time_days = fields.Integer(string='Lead Time (days)')
    pm_availability_date = fields.Date(string='Availability Date')

    # Compliance
    pm_hs_code = fields.Char(string='HS Code')
    pm_country_of_origin = fields.Char(string='Country of Origin')
    pm_reach_compliant = fields.Boolean(string='REACH Compliant')
    pm_rohs_compliant = fields.Boolean(string='RoHS Compliant')
    pm_ce_marked = fields.Boolean(string='CE Marked')
    pm_weee_category = fields.Char(string='WEEE Category')

    # Environmental
    pm_energy_class = fields.Char(string='Energy Class')
    pm_power_consumption = fields.Float(string='Power Consumption (kWh)')
    pm_recyclable = fields.Boolean(string='Recyclable')
    pm_eco_friendly = fields.Boolean(string='Eco-Friendly')
    pm_carbon_footprint = fields.Float(string='Carbon Footprint (kg CO₂)')

    # Dimensions
    pm_weight = fields.Float(string='PM Weight (kg)')
    pm_width = fields.Float(string='Width (cm)')
    pm_height = fields.Float(string='Height (cm)')
    pm_depth = fields.Float(string='Depth (cm)')
    pm_package_length = fields.Float(string='Pkg Length (cm)')
    pm_package_width = fields.Float(string='Pkg Width (cm)')
    pm_package_height = fields.Float(string='Pkg Height (cm)')
    pm_package_weight = fields.Float(string='Pkg Weight (g)')

    # Lifecycle
    pm_lifecycle_status = fields.Char(string='Lifecycle Status')
    pm_discontinued = fields.Boolean(string='Discontinued')
    pm_end_of_life_date = fields.Date(string='End of Life Date')

    # Descriptions
    pm_description = fields.Text(string='PM Description')
    pm_short_description = fields.Text(string='PM Short Description')

    # Technical Specs
    pm_technical_specs_json = fields.Text(string='Technical Specs JSON')
    pm_technical_specs_html = fields.Html(
        string='Technical Specs',
        compute='_compute_pm_technical_specs_html',
        sanitize=False,
    )

    # AI Enrichment
    pm_ai_category = fields.Char(string='AI Category')
    pm_ai_short_desc = fields.Text(string='AI Short Description')
    pm_ai_long_desc = fields.Text(string='AI Long Description')
    pm_ai_seo_title = fields.Char(string='AI SEO Title')
    pm_ai_seo_meta = fields.Text(string='AI SEO Meta Description')
    pm_ai_quality_score = fields.Float(string='AI Quality Score')

    # Images
    pm_image_urls_json = fields.Text(string='PM Image URLs JSON')
    pm_image_main = fields.Binary(string='PM Main Image', attachment=True)
    pm_image_2 = fields.Binary(string='PM Image 2', attachment=True)
    pm_image_3 = fields.Binary(string='PM Image 3', attachment=True)

    # Categories
    pm_category_name = fields.Char(string='PM Category')
    pm_google_category = fields.Char(string='Google Category')
    pm_amazon_category = fields.Char(string='Amazon Category')

    # Analytics
    pm_popularity_score = fields.Float(string='Popularity Score')
    pm_sales_count = fields.Integer(string='Sales Count')
    pm_view_count = fields.Integer(string='View Count')

    # Status
    pm_status = fields.Char(string='PM Status')
    pm_is_active_pm = fields.Boolean(string='Active in PM', default=True)

    # Last full fetch
    pm_last_full_fetch = fields.Datetime(string='Last Full Fetch', readonly=True)

    # ── Computed fields ────────────────────────────────────────────────

    @api.depends('seller_ids', 'seller_ids.price', 'seller_ids.partner_id')
    def _compute_pm_supplier_prices_html(self):
        for product in self:
            sellers = product.seller_ids
            if not sellers:
                product.pm_supplier_prices_html = '<em>No supplier info</em>'
                continue
            rows = []
            for s in sellers:
                rows.append(
                    f'<tr><td>{s.partner_id.name}</td>'
                    f'<td style="text-align:right">{s.price:.2f} {s.currency_id.symbol}</td>'
                    f'<td style="text-align:right">{int(s.min_qty)}</td></tr>'
                )
            html = (
                '<table class="table table-sm table-striped mb-0">'
                '<thead><tr><th>Supplier</th><th>Price</th><th>MOQ</th></tr></thead>'
                '<tbody>' + ''.join(rows) + '</tbody></table>'
            )
            product.pm_supplier_prices_html = html

    @api.depends('pm_competitor_prices_json')
    def _compute_pm_competitor_prices_html(self):
        for product in self:
            if not product.pm_competitor_prices_json:
                product.pm_competitor_prices_html = '<em>No competitor data</em>'
                continue
            try:
                data = json.loads(product.pm_competitor_prices_json)
            except (json.JSONDecodeError, TypeError):
                product.pm_competitor_prices_html = '<em>Invalid data</em>'
                continue
            rows = []
            for key, val in data.items():
                if val:
                    label = key.replace('competitor_price_', '').replace('_', ' ').title()
                    rows.append(
                        f'<tr><td>{label}</td>'
                        f'<td style="text-align:right">{float(val):.2f} €</td></tr>'
                    )
            if rows:
                product.pm_competitor_prices_html = (
                    '<table class="table table-sm table-striped mb-0">'
                    '<thead><tr><th>Competitor</th><th>Price</th></tr></thead>'
                    '<tbody>' + ''.join(rows) + '</tbody></table>'
                )
            else:
                product.pm_competitor_prices_html = '<em>No competitor prices</em>'

    @api.depends('pm_technical_specs_json')
    def _compute_pm_technical_specs_html(self):
        for product in self:
            if not product.pm_technical_specs_json:
                product.pm_technical_specs_html = '<em>No technical specs</em>'
                continue
            try:
                specs = json.loads(product.pm_technical_specs_json)
            except (json.JSONDecodeError, TypeError):
                product.pm_technical_specs_html = '<em>Invalid data</em>'
                continue
            if isinstance(specs, dict):
                rows = ''.join(
                    f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in specs.items()
                )
                product.pm_technical_specs_html = (
                    '<table class="table table-sm table-striped mb-0">'
                    '<thead><tr><th>Spec</th><th>Value</th></tr></thead>'
                    '<tbody>' + rows + '</tbody></table>'
                )
            else:
                product.pm_technical_specs_html = (
                    f'<pre>{json.dumps(specs, indent=2)}</pre>'
                )

    # ── Button Actions ─────────────────────────────────────────────────

    def action_update_pm_prices(self):
        """Update prices & stock from PM API for this product."""
        self.ensure_one()
        if not self.pm_external_id:
            raise UserError(_(
                'This product has no PM External ID. '
                'It was not imported from Products Manager.'
            ))
        config = self.env['pm.config'].get_active_config()
        api = config._get_api_client()
        try:
            pm_data = api.get_product(self.pm_external_id)
            config._update_product_from_pm(self, pm_data, api)
        except ProductsManagerAPIError as exc:
            raise UserError(_('Failed to update from PM: %s') % exc)

        self.env['pm.sync.log'].log(
            config_id=config.id,
            operation='sync',
            message=(
                f'Manual price/stock update for {self.name} '
                f'(PM {self.pm_external_id})'
            ),
            product_count=1,
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Prices & Stock Updated'),
                'message': _(
                    'Product prices and stock have been refreshed '
                    'from Products Manager.'
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_fetch_full_pm_data(self):
        """Fetch ALL PM data for this product and store in fields."""
        self.ensure_one()
        if not self.pm_external_id:
            raise UserError(_('This product has no PM External ID.'))

        config = self.env['pm.config'].get_active_config()
        api = config._get_api_client()

        try:
            pm_data = api.get_product(self.pm_external_id)
        except ProductsManagerAPIError as exc:
            raise UserError(_('Failed to fetch PM data: %s') % exc)

        vals = self._map_full_pm_data(pm_data)

        # Also try enrichment endpoint
        try:
            enrichment = api.get_enrichment(self.pm_external_id)
            if isinstance(enrichment, dict):
                vals.update(self._map_enrichment_data(enrichment))
        except ProductsManagerAPIError:
            _logger.debug('Enrichment endpoint not available for %s', self.pm_external_id)

        # Also update prices & stock
        try:
            config._update_product_from_pm(self, pm_data, api)
        except Exception:
            _logger.warning('Price/stock update failed during full fetch', exc_info=True)

        # Download images
        self._download_pm_images(pm_data)

        vals['pm_last_full_fetch'] = fields.Datetime.now()
        self.write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Full PM Data Fetched'),
                'message': _(
                    'All product data has been retrieved from Products Manager.'
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # ── Mapping helpers ────────────────────────────────────────────────

    def _map_full_pm_data(self, pm_data):
        """Map PM API ProductDetailResponse to stored field values."""
        # Competitor prices
        competitor_prices = {}
        for key in [
            'competitor_price_amazon', 'competitor_price_cdiscount',
            'competitor_price_fnac', 'competitor_price_darty',
            'competitor_price_boulanger', 'competitor_price_ubaldi',
            'competitor_price_rueducommerce', 'competitor_price_conforama',
            'competitor_price_but', 'competitor_price_electrodepot',
        ]:
            val = pm_data.get(key)
            if val:
                competitor_prices[key] = float(val)

        ai_meta = pm_data.get('ai_metadata') or {}
        if isinstance(ai_meta, list) and ai_meta:
            ai_meta = ai_meta[0]
        elif not isinstance(ai_meta, dict):
            ai_meta = {}

        return {
            # Identity
            'pm_ean': pm_data.get('ean') or '',
            'pm_asin': pm_data.get('asin') or '',
            'pm_upc': pm_data.get('upc') or '',
            'pm_gtin': pm_data.get('gtin') or '',
            'pm_manufacturer_ref': pm_data.get('manufacturer_reference') or '',
            'pm_manufacturer': pm_data.get('manufacturer') or '',
            'pm_brand': (
                pm_data.get('brand_name')
                or pm_data.get('brand')
                or ''
            ),
            # Pricing
            'pm_cost_price': _safe_float(pm_data.get('cost_price')),
            'pm_recommended_price': _safe_float(pm_data.get('recommended_price')),
            'pm_margin': _safe_float(pm_data.get('margin')),
            # Competitor prices
            'pm_competitor_prices_json': (
                json.dumps(competitor_prices) if competitor_prices else False
            ),
            # Stock
            'pm_stock_quantity': _safe_int(pm_data.get('stock_quantity')),
            'pm_is_low_stock': bool(pm_data.get('is_low_stock')),
            'pm_min_order_qty': _safe_int(pm_data.get('min_order_quantity')),
            'pm_max_order_qty': _safe_int(pm_data.get('max_order_quantity')),
            'pm_lead_time_days': _safe_int(pm_data.get('lead_time_days')),
            'pm_availability_date': pm_data.get('availability_date') or False,
            # Compliance
            'pm_hs_code': pm_data.get('hs_code') or '',
            'pm_country_of_origin': pm_data.get('country_of_origin') or '',
            'pm_reach_compliant': bool(pm_data.get('reach_compliant')),
            'pm_rohs_compliant': bool(pm_data.get('rohs_compliant')),
            'pm_ce_marked': bool(pm_data.get('ce_marked')),
            'pm_weee_category': pm_data.get('weee_category') or '',
            # Environmental
            'pm_energy_class': pm_data.get('energy_class') or '',
            'pm_power_consumption': _safe_float(pm_data.get('power_consumption_kwh')),
            'pm_recyclable': bool(pm_data.get('recyclable')),
            'pm_eco_friendly': bool(pm_data.get('eco_friendly')),
            'pm_carbon_footprint': _safe_float(pm_data.get('carbon_footprint_kg')),
            # Dimensions
            'pm_weight': _safe_float(pm_data.get('weight')),
            'pm_width': _safe_float(pm_data.get('width')),
            'pm_height': _safe_float(pm_data.get('height')),
            'pm_depth': _safe_float(pm_data.get('depth')),
            'pm_package_length': _safe_float(pm_data.get('package_length')),
            'pm_package_width': _safe_float(pm_data.get('package_width')),
            'pm_package_height': _safe_float(pm_data.get('package_height')),
            'pm_package_weight': _safe_float(pm_data.get('package_weight')),
            # Lifecycle
            'pm_lifecycle_status': pm_data.get('lifecycle_status') or '',
            'pm_discontinued': bool(pm_data.get('discontinued')),
            'pm_end_of_life_date': pm_data.get('end_of_life_date') or False,
            # Descriptions
            'pm_description': pm_data.get('description') or '',
            'pm_short_description': pm_data.get('short_description') or '',
            # Technical
            'pm_technical_specs_json': (
                json.dumps(pm_data['technical_specs'])
                if pm_data.get('technical_specs')
                else False
            ),
            # AI Enrichment
            'pm_ai_category': ai_meta.get('ai_category') or '',
            'pm_ai_short_desc': ai_meta.get('ai_short_description') or '',
            'pm_ai_long_desc': ai_meta.get('ai_long_description') or '',
            'pm_ai_seo_title': ai_meta.get('seo_title') or '',
            'pm_ai_seo_meta': ai_meta.get('seo_meta_description') or '',
            'pm_ai_quality_score': _safe_float(ai_meta.get('overall_quality_score')),
            # Categories
            'pm_category_name': pm_data.get('category_name') or '',
            'pm_google_category': pm_data.get('google_category') or '',
            'pm_amazon_category': pm_data.get('amazon_category') or '',
            # Analytics
            'pm_popularity_score': _safe_float(pm_data.get('popularity_score')),
            'pm_sales_count': _safe_int(pm_data.get('sales_count')),
            'pm_view_count': _safe_int(pm_data.get('view_count')),
            # Status
            'pm_status': pm_data.get('status') or '',
            'pm_is_active_pm': bool(pm_data.get('is_active', True)),
            # Completeness
            'pm_completeness': _safe_int(pm_data.get('completeness_score')),
            # Image URLs
            'pm_image_urls_json': (
                json.dumps(pm_data['images'])
                if pm_data.get('images')
                else False
            ),
        }

    def _map_enrichment_data(self, enrichment):
        """Map enrichment API response to field vals."""
        vals = {}
        if enrichment.get('ai_short_description'):
            vals['pm_ai_short_desc'] = enrichment['ai_short_description']
        if enrichment.get('ai_long_description'):
            vals['pm_ai_long_desc'] = enrichment['ai_long_description']
        if enrichment.get('ai_category'):
            vals['pm_ai_category'] = enrichment['ai_category']
        if enrichment.get('seo_title'):
            vals['pm_ai_seo_title'] = enrichment['seo_title']
        if enrichment.get('seo_meta_description'):
            vals['pm_ai_seo_meta'] = enrichment['seo_meta_description']
        if enrichment.get('overall_quality_score'):
            vals['pm_ai_quality_score'] = _safe_float(
                enrichment['overall_quality_score']
            )
        return vals

    def _download_pm_images(self, pm_data):
        """Download PM images and store as binary fields."""
        images = pm_data.get('images') or []
        if not images:
            return
        for i, img in enumerate(images[:3]):
            url = ''
            if isinstance(img, dict):
                url = img.get('url') or img.get('src') or img.get('original') or ''
            elif isinstance(img, str):
                url = img
            if not url:
                continue
            try:
                resp = http_requests.get(url, timeout=15)
                if resp.status_code == 200 and resp.content:
                    b64 = base64.b64encode(resp.content)
                    if i == 0:
                        self.write({'pm_image_main': b64, 'image_1920': b64})
                    elif i == 1:
                        self.write({'pm_image_2': b64})
                    elif i == 2:
                        self.write({'pm_image_3': b64})
            except Exception:
                _logger.debug('Failed to download PM image %s', url)
