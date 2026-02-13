import logging
import requests
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class PrestaShopInstance(models.Model):
    _name = 'prestashop.instance'
    _description = 'PrestaShop Instance'

    name = fields.Char(required=True)
    url = fields.Char(string='Store URL', required=True, help="Include /api/ at the end (e.g., https://mystore.com/api/)")
    api_key = fields.Char(string='API Key', required=True)
    active = fields.Boolean(default=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    last_sync_date = fields.Datetime(string='Last Sync Date', readonly=True)
    order_ids = fields.One2many('sale.order', 'prestashop_instance_id', string='Synchronized Orders')

    def action_test_connection(self):
        """Test connection to PrestaShop API"""
        self.ensure_one()
        try:
            url = f"{self.url}?schema=blank"
            response = requests.get(url, auth=(self.api_key, ''), timeout=10)
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
                raise UserError(_("Connection failed. Status Code: %s") % response.status_code)
        except Exception as e:
            raise UserError(_("Connection error: %s") % str(e))

    def _api_get(self, resource, resource_id=None, params=None):
        """Helper method to make API calls to PrestaShop"""
        self.ensure_one()
        try:
            if resource_id:
                url = f"{self.url}/{resource}/{resource_id}"
            else:
                url = f"{self.url}/{resource}"

            response = requests.get(url, auth=(self.api_key, ''), params=params, timeout=30)
            if response.status_code == 200:
                return ET.fromstring(response.content)
            else:
                _logger.error(f"PrestaShop API error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            _logger.error(f"PrestaShop API call failed: {str(e)}")
            return None

    def _find_or_create_customer(self, customer_id):
        """Find or create customer from PrestaShop"""
        self.ensure_one()

        # Check if customer already exists in Odoo
        partner = self.env['res.partner'].search([
            ('comment', 'ilike', f'PrestaShop ID: {customer_id}')
        ], limit=1)

        if partner:
            return partner

        # Fetch customer from PrestaShop
        customer_xml = self._api_get('customers', customer_id)
        if customer_xml is None:
            return None

        customer = customer_xml.find('.//customer')
        if customer is None:
            return None

        # Extract customer data
        firstname = customer.find('firstname').text or ''
        lastname = customer.find('lastname').text or ''
        email = customer.find('email').text or ''

        # Create partner in Odoo
        partner = self.env['res.partner'].create({
            'name': f"{firstname} {lastname}".strip(),
            'email': email,
            'comment': f'PrestaShop ID: {customer_id}',
            'customer_rank': 1,
        })

        _logger.info(f"Created customer {partner.name} from PrestaShop ID {customer_id}")
        return partner

    def _find_or_create_product(self, product_id, product_name, product_reference):
        """Find or create product from PrestaShop"""
        self.ensure_one()

        # Try to find by reference first
        product = None
        if product_reference:
            product = self.env['product.product'].search([
                ('default_code', '=', product_reference)
            ], limit=1)

        # Try to find by name
        if not product:
            product = self.env['product.product'].search([
                ('name', '=', product_name)
            ], limit=1)

        # Create if not found
        if not product:
            product = self.env['product.product'].create({
                'name': product_name,
                'default_code': product_reference,
                'type': 'product',
                'list_price': 0.0,
                'description_sale': f'PrestaShop Product ID: {product_id}',
            })
            _logger.info(f"Created product {product.name} from PrestaShop ID {product_id}")

        return product

    def action_sync_orders(self):
        """Synchronize orders from PrestaShop (last 30 days)"""
        self.ensure_one()

        try:
            # Calculate date 30 days ago
            date_from = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

            # Fetch orders from PrestaShop
            params = {
                'filter[date_add]': f'[{date_from},]',
                'display': 'full',
            }

            orders_xml = self._api_get('orders', params=params)
            if orders_xml is None:
                raise UserError(_("Failed to fetch orders from PrestaShop"))

            orders = orders_xml.findall('.//order')
            imported_count = 0
            skipped_count = 0

            for order in orders:
                order_id = order.find('id').text

                # Check if order already imported
                existing = self.env['sale.order'].search([
                    ('prestashop_order_id', '=', order_id),
                    ('prestashop_instance_id', '=', self.id)
                ], limit=1)

                if existing:
                    skipped_count += 1
                    continue

                # Get order details
                order_reference = order.find('reference').text or f'PS-{order_id}'
                customer_id = order.find('id_customer').text
                total_paid = float(order.find('total_paid').text or 0)
                date_add = order.find('date_add').text

                # Find or create customer
                partner = self._find_or_create_customer(customer_id)
                if not partner:
                    _logger.warning(f"Could not create customer for order {order_id}")
                    continue

                # Create sale order
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

                # Fetch order details for lines
                order_detail_xml = self._api_get('orders', order_id)
                if order_detail_xml:
                    associations = order_detail_xml.find('.//associations')
                    if associations is not None:
                        order_rows = associations.findall('.//order_row')

                        for row in order_rows:
                            product_id = row.find('product_id').text
                            product_name = row.find('product_name').text or 'Unknown Product'
                            product_reference = row.find('product_reference').text or ''
                            quantity = int(row.find('product_quantity').text or 1)
                            unit_price = float(row.find('product_price').text or 0)

                            # Find or create product
                            product = self._find_or_create_product(product_id, product_name, product_reference)

                            # Create order line
                            self.env['sale.order.line'].create({
                                'order_id': sale_order.id,
                                'product_id': product.id,
                                'name': product_name,
                                'product_uom_qty': quantity,
                                'price_unit': unit_price,
                            })

                imported_count += 1
                _logger.info(f"Imported PrestaShop order {order_id} as {sale_order.name}")

            # Update last sync date
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
            _logger.error(f"Order sync failed: {str(e)}")
            raise UserError(_("Synchronization failed: %s") % str(e))
