import logging
import requests
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopInstance(models.Model):
    _name = 'prestashop.instance'
    _description = 'PrestaShop Instance'

    name = fields.Char(required=True)
    url = fields.Char(string='Store URL', required=True, help="PrestaShop API URL (e.g., https://mystore.com/api/)")
    api_key = fields.Char(string='API Key', required=True)
    active = fields.Boolean(default=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    last_sync_date = fields.Datetime(string='Last Sync Date', readonly=True)
    order_ids = fields.One2many('sale.order', 'prestashop_instance_id', string='Synchronized Orders')

    def _get_base_url(self):
        """Return clean base URL for API calls"""
        return self.url.rstrip('/')

    def action_test_connection(self):
        """Test connection to PrestaShop API"""
        self.ensure_one()
        try:
            base_url = self._get_base_url()
            response = requests.get(
                base_url,
                auth=(self.api_key, ''),
                params={'output_format': 'JSON'},
                timeout=10,
            )
            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection successful to PrestaShop API!'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_("Connection failed. Status Code: %s\nResponse: %s") % (
                    response.status_code, response.text[:300]))
        except requests.exceptions.RequestException as e:
            raise UserError(_("Connection error: %s") % str(e))

    def _api_get(self, resource, resource_id=None, params=None):
        """Helper method to make JSON API calls to PrestaShop"""
        self.ensure_one()
        base_url = self._get_base_url()

        if resource_id:
            url = f"{base_url}/{resource}/{resource_id}"
        else:
            url = f"{base_url}/{resource}"

        if params is None:
            params = {}
        params['output_format'] = 'JSON'

        _logger.info("PrestaShop API call: %s with params: %s", url, params)
        response = requests.get(url, auth=(self.api_key, ''), params=params, timeout=30)
        _logger.info("PrestaShop API response: Status %s", response.status_code)

        if response.status_code != 200:
            _logger.error("PrestaShop API error %s: %s", response.status_code, response.text[:500])
            raise UserError(_("PrestaShop API error (status %s). URL: %s") % (
                response.status_code, url))

        try:
            return response.json()
        except Exception:
            _logger.error("Failed to parse JSON response: %s", response.text[:500])
            raise UserError(_("Invalid API response (not JSON). URL: %s\nResponse: %s") % (
                url, response.text[:300]))

    def action_check_permissions(self):
        """Check which resources are available on the PrestaShop API"""
        self.ensure_one()
        try:
            data = self._api_get('')
            if isinstance(data, dict):
                resources = list(data.keys())
                msg = ', '.join(resources[:30])
            else:
                msg = str(data)[:500]
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Available API Resources'),
                    'message': msg,
                    'type': 'info',
                    'sticky': True,
                }
            }
        except Exception as e:
            raise UserError(_("Check failed: %s") % str(e))

    def _find_or_create_customer(self, customer_id):
        """Find or create customer from PrestaShop"""
        self.ensure_one()
        try:
            partner = self.env['res.partner'].search([
                ('comment', 'ilike', f'PrestaShop ID: {customer_id}')
            ], limit=1)
            if partner:
                return partner

            data = self._api_get('customers', customer_id)
            customer = data.get('customer', {})

            firstname = customer.get('firstname', '')
            lastname = customer.get('lastname', '')
            email = customer.get('email', '')

            partner = self.env['res.partner'].create({
                'name': f"{firstname} {lastname}".strip() or f'Customer {customer_id}',
                'email': email,
                'comment': f'PrestaShop ID: {customer_id}',
                'customer_rank': 1,
            })
            _logger.info("Created customer %s from PrestaShop ID %s", partner.name, customer_id)
            return partner

        except Exception as e:
            _logger.error("Error creating customer %s: %s", customer_id, str(e))
            return None

    def _find_or_create_product(self, product_id, product_name, product_reference):
        """Find or create product from PrestaShop"""
        self.ensure_one()
        product = None

        if product_reference:
            product = self.env['product.product'].search([
                ('default_code', '=', product_reference)
            ], limit=1)

        if not product and product_name:
            product = self.env['product.product'].search([
                ('name', '=', product_name)
            ], limit=1)

        if not product:
            product = self.env['product.product'].create({
                'name': product_name or f'Product {product_id}',
                'default_code': product_reference,
                'type': 'product',
                'list_price': 0.0,
                'description_sale': f'PrestaShop Product ID: {product_id}',
            })
            _logger.info("Created product %s from PrestaShop ID %s", product.name, product_id)

        return product

    def action_sync_orders(self):
        """Synchronize orders from PrestaShop (last 50 orders)"""
        self.ensure_one()

        try:
            data = self._api_get('orders', params={
                'display': 'full',
                'limit': 50,
                'sort': '[id_DESC]',
            })

            orders = data.get('orders', [])
            if isinstance(orders, dict):
                orders = [orders]

            if not orders:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('No Orders'),
                        'message': _('No orders found in PrestaShop'),
                        'type': 'info',
                        'sticky': False,
                    }
                }

            imported_count = 0
            skipped_count = 0

            for order in orders:
                order_id = str(order.get('id', ''))

                existing = self.env['sale.order'].search([
                    ('prestashop_order_id', '=', order_id),
                    ('prestashop_instance_id', '=', self.id)
                ], limit=1)

                if existing:
                    skipped_count += 1
                    continue

                order_reference = order.get('reference', f'PS-{order_id}')
                customer_id = str(order.get('id_customer', ''))
                date_add = order.get('date_add', '')

                partner = self._find_or_create_customer(customer_id)
                if not partner:
                    _logger.warning("Could not create customer for order %s", order_id)
                    continue

                sale_order = self.env['sale.order'].create({
                    'partner_id': partner.id,
                    'date_order': date_add,
                    'warehouse_id': self.warehouse_id.id,
                    'company_id': self.company_id.id,
                    'prestashop_instance_id': self.id,
                    'prestashop_order_id': order_id,
                    'prestashop_source': 'prestashop',
                    'origin': order_reference,
                })

                # Process order lines from associations
                associations = order.get('associations', {})
                order_rows = associations.get('order_rows', [])
                if isinstance(order_rows, dict):
                    order_rows = [order_rows]

                for row in order_rows:
                    product_id = str(row.get('product_id', ''))
                    product_name = row.get('product_name', 'Unknown Product')
                    product_reference = row.get('product_reference', '')
                    quantity = int(row.get('product_quantity', 1))
                    unit_price = float(row.get('product_price', 0))

                    product = self._find_or_create_product(product_id, product_name, product_reference)

                    self.env['sale.order.line'].create({
                        'order_id': sale_order.id,
                        'product_id': product.id,
                        'name': product_name,
                        'product_uom_qty': quantity,
                        'price_unit': unit_price,
                    })

                imported_count += 1
                _logger.info("Imported PrestaShop order %s as %s", order_id, sale_order.name)

            self.last_sync_date = fields.Datetime.now()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Synchronization Complete'),
                    'message': _('Imported: %d orders, Skipped: %d already imported') % (imported_count, skipped_count),
                    'type': 'success',
                    'sticky': False,
                }
            }

        except Exception as e:
            _logger.error("Order sync failed: %s", str(e))
            raise UserError(_("Synchronization failed: %s") % str(e))
