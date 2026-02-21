import logging
from datetime import datetime, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .pm_api import ProductsManagerAPI, ProductsManagerAPIError

_logger = logging.getLogger(__name__)


class PmConfig(models.Model):
    _name = 'pm.config'
    _description = 'Products Manager Configuration'
    _rec_name = 'name'

    name = fields.Char(default='Products Manager', required=True)
    base_url = fields.Char(
        string='API URL',
        default='https://api.productsmanager.app/api/v1',
        required=True,
    )
    email = fields.Char(string='Email', required=True)
    password = fields.Char(string='Password', required=True)
    access_token = fields.Char(string='Access Token', readonly=True, copy=False)
    token_expiry = fields.Datetime(string='Token Expiry', readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    auto_sync = fields.Boolean(string='Auto Sync', default=True)
    sync_interval = fields.Integer(string='Sync Interval (hours)', default=6)
    last_sync_date = fields.Datetime(string='Last Sync', readonly=True)
    state = fields.Selection([
        ('draft', 'Not Connected'),
        ('connected', 'Connected'),
        ('error', 'Error'),
    ], default='draft', readonly=True, copy=False)
    last_error = fields.Text(string='Last Error', readonly=True)

    # ── API helpers ─────────────────────────────────────────────────────

    @api.model
    def get_active_config(self):
        """Return the active configuration for the current company."""
        config = self.search([
            ('active', '=', True),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not config:
            config = self.search([('active', '=', True)], limit=1)
        if not config:
            raise UserError(_('No active Products Manager configuration found. '
                              'Go to Products Manager > Configuration to set one up.'))
        return config

    def _refresh_token(self):
        """Authenticate against the PM API and store the JWT token."""
        self.ensure_one()
        api = ProductsManagerAPI(self.base_url)
        try:
            token = api.login(self.email, self.password)
            self.write({
                'access_token': token,
                'token_expiry': fields.Datetime.now() + timedelta(hours=23),
                'state': 'connected',
                'last_error': False,
            })
            return token
        except ProductsManagerAPIError as exc:
            self.write({
                'state': 'error',
                'last_error': str(exc),
            })
            raise UserError(_('Authentication failed: %s') % exc)

    def _get_api_client(self):
        """Return a ProductsManagerAPI instance with a valid token."""
        self.ensure_one()
        if not self.access_token or (self.token_expiry and self.token_expiry < fields.Datetime.now()):
            self._refresh_token()
        return ProductsManagerAPI(self.base_url, self.access_token)

    # ── Actions ─────────────────────────────────────────────────────────

    def action_test_connection(self):
        """Test the API connection."""
        self.ensure_one()
        try:
            api = ProductsManagerAPI(self.base_url)
            api.login(self.email, self.password)
            ok, msg = api.test_connection()
            if ok:
                self.write({
                    'access_token': api.token,
                    'token_expiry': fields.Datetime.now() + timedelta(hours=23),
                    'state': 'connected',
                    'last_error': False,
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Connection Successful'),
                        'message': _('Successfully connected to Products Manager API.'),
                        'type': 'success',
                        'sticky': False,
                    },
                }
            else:
                self.write({'state': 'error', 'last_error': msg})
                raise UserError(_('Connection test failed: %s') % msg)
        except ProductsManagerAPIError as exc:
            self.write({'state': 'error', 'last_error': str(exc)})
            raise UserError(_('Connection failed: %s') % exc)

    def action_sync_now(self):
        """Manually trigger price & stock sync."""
        self.ensure_one()
        self._sync_prices_and_stock()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sync Complete'),
                'message': _('Prices and stock have been synchronized.'),
                'type': 'success',
                'sticky': False,
            },
        }

    @api.model
    def action_open_dashboard(self):
        """Open the dashboard: show existing config or create form."""
        config = self.search([('active', '=', True)], limit=1)
        if config:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Products Manager'),
                'res_model': 'pm.config',
                'res_id': config.id,
                'view_mode': 'form',
                'view_id': self.env.ref('productsmanager_odoo.pm_dashboard_view_form').id,
            }
        # No config yet — open config creation form
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Configuration'),
            'res_model': 'pm.config',
            'view_mode': 'form',
        }

    def action_open_field_mapping(self):
        """Open field mapping list filtered for this config."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Field Mapping'),
            'res_model': 'pm.field.mapping',
            'view_mode': 'list,form',
            'context': {'default_config_id': self.id, 'search_default_config_id': self.id},
        }

    def action_open_search_wizard(self):
        """Open the search & import wizard."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Search & Import'),
            'res_model': 'pm.import.wizard',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_config_id': self.id},
        }

    # ── Cron ────────────────────────────────────────────────────────────

    @api.model
    def _cron_sync_products(self):
        """Periodic sync: update prices & stock for all imported products."""
        configs = self.search([('active', '=', True), ('auto_sync', '=', True)])
        for config in configs:
            try:
                config._sync_prices_and_stock()
            except Exception as exc:
                _logger.error('Products Manager sync error for %s: %s', config.name, exc)
                self.env['pm.sync.log'].log(
                    config_id=config.id,
                    operation='error',
                    message=f'Sync failed: {exc}',
                )

    def _sync_prices_and_stock(self):
        """Update prices and stock for all products imported from PM."""
        self.ensure_one()
        api = self._get_api_client()
        products = self.env['product.product'].search([
            ('pm_external_id', '!=', False),
        ])
        updated = 0
        errors = 0
        for product in products:
            try:
                pm_data = api.get_product(product.pm_external_id)
                self._update_product_from_pm(product, pm_data, api)
                updated += 1
            except ProductsManagerAPIError as exc:
                _logger.warning('Failed to sync product %s: %s', product.pm_external_id, exc)
                errors += 1
            except Exception as exc:
                _logger.error('Unexpected error syncing product %s: %s', product.pm_external_id, exc)
                errors += 1

        self.write({'last_sync_date': fields.Datetime.now()})
        self.env['pm.sync.log'].log(
            config_id=self.id,
            operation='sync',
            message=f'Sync complete: {updated} updated, {errors} errors',
            product_count=updated,
        )

    def _update_product_from_pm(self, product, pm_data, api):
        """Update a single product from PM data (prices + stock)."""
        suppliers_data = self._extract_suppliers(pm_data)
        if not suppliers_data:
            try:
                suppliers_raw = api.get_product_suppliers(product.pm_external_id)
                suppliers_data = self._normalize_suppliers(suppliers_raw)
            except ProductsManagerAPIError:
                pass

        if suppliers_data:
            self._update_supplier_info(product, suppliers_data)

        product.write({'pm_last_sync': fields.Datetime.now()})

    def _extract_suppliers(self, pm_data):
        """Extract supplier data from a PM product response."""
        suppliers = []
        raw = pm_data.get('suppliers') or pm_data.get('tarification') or []
        if isinstance(raw, list):
            for s in raw:
                suppliers.append({
                    'name': s.get('name') or s.get('supplier_name') or s.get('supplier') or 'Unknown',
                    'price': float(s.get('price') or s.get('prix') or 0),
                    'stock': int(s.get('stock') or s.get('quantity') or 0),
                    'moq': int(s.get('moq') or s.get('min_qty') or 1),
                    'is_best': bool(s.get('is_best') or s.get('best')),
                })
        return suppliers

    def _normalize_suppliers(self, suppliers_raw):
        """Normalize raw supplier API response."""
        if isinstance(suppliers_raw, list):
            return self._extract_suppliers({'suppliers': suppliers_raw})
        return []

    def _update_supplier_info(self, product, suppliers_data):
        """Create or update product.supplierinfo records from PM supplier data."""
        SupplierInfo = self.env['product.supplierinfo']
        best_price = None

        for s_data in suppliers_data:
            partner = self._get_or_create_supplier_partner(s_data['name'])
            existing = SupplierInfo.search([
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ('partner_id', '=', partner.id),
            ], limit=1)

            vals = {
                'price': s_data['price'],
                'min_qty': s_data.get('moq', 1),
                'delay': 1,
                'pm_supplier_stock': s_data.get('stock', 0),
                'pm_is_best': s_data.get('is_best', False),
            }

            if existing:
                existing.write(vals)
            else:
                vals.update({
                    'product_tmpl_id': product.product_tmpl_id.id,
                    'partner_id': partner.id,
                })
                SupplierInfo.create(vals)

            if s_data['price'] > 0 and (best_price is None or s_data['price'] < best_price):
                best_price = s_data['price']

        if best_price:
            product.product_tmpl_id.write({'standard_price': best_price})

    def _get_or_create_supplier_partner(self, name):
        """Find or create a supplier res.partner by name."""
        Partner = self.env['res.partner']
        partner = Partner.search([
            ('name', '=ilike', name),
            ('supplier_rank', '>', 0),
        ], limit=1)
        if not partner:
            partner = Partner.create({
                'name': name,
                'supplier_rank': 1,
                'company_type': 'company',
            })
        return partner

    @api.model
    def _cron_clean_logs(self):
        """Remove sync logs older than 30 days."""
        cutoff = fields.Datetime.now() - timedelta(days=30)
        old_logs = self.env['pm.sync.log'].search([('create_date', '<', cutoff)])
        old_logs.unlink()
