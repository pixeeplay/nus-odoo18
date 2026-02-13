import logging
import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import xml.etree.ElementTree as ET

_logger = logging.getLogger(__name__)

class PrestaShopInstance(models.Model):
    _name = 'prestashop.instance'
    _description = 'PrestaShop Instance'

    name = fields.Char(required=True)
    url = fields.Char(string='Store URL', required=True, help="Include trailing slash (e.g., https://mystore.com/api/)")
    api_key = fields.Char(string='API Key', required=True)
    active = fields.Boolean(default=True)
    last_sync_date = fields.Datetime(string='Last Sync Date')
    auto_sync = fields.Boolean(string='Hourly Auto-Sync', default=False,
        help="Enable automatic hourly synchronization. Orders will be imported automatically without preview.")
    
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True)

    # Preview Orders Relation
    preview_order_ids = fields.One2many('prestashop.order.preview', 'instance_id', string='Preview Orders')

    def action_test_connection(self):
        self.ensure_one()
        try:
            url = f"{self.url}api/?schema=blank"
            response = requests.get(url, auth=(self.api_key, ''), timeout=10)
            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection successful to PrestaShop.'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_("Connection failed. Status Code: %s") % response.status_code)
        except Exception as e:
            raise UserError(_("Connection error: %s") % str(e))

    def _api_get(self, resource, resource_id=None, params=None):
        """Helper to fetch data from PrestaShop Webservice"""
        url = f"{self.url}api/{resource}"
        if resource_id:
            url += f"/{resource_id}"
        
        if not params:
            params = {}
            
        try:
            response = requests.get(url, auth=(self.api_key, ''), params=params, timeout=20)
            if response.status_code == 200:
                return ET.fromstring(response.content)
            else:
                _logger.error("PrestaShop API Error: %s - %s", response.status_code, response.text)
                return None
        except Exception as e:
            _logger.error("PrestaShop Connection Error: %s", str(e))
            return None

    def _cron_sync_orders(self):
        """Triggered by cron for all active instances"""
        instances = self.search([('active', '=', True), ('auto_sync', '=', True)])
        for instance in instances:
            instance.action_sync_now()

    def _find_or_create_customer(self, customer_id):
        """Sync a single customer from PrestaShop"""
        xml = self._api_get('customers', customer_id, params={'display': 'full'})
        if xml is None: return None
        
        cust_data = xml.find('customer')
        email = cust_data.find('email').text
        first_name = cust_data.find('firstname').text
        last_name = cust_data.find('lastname').text
        
        partner = self.env['res.partner'].search([('email', '=', email)], limit=1)
        if not partner:
            partner = self.env['res.partner'].create({
                'name': f"{first_name} {last_name}",
                'email': email,
                'company_type': 'person',
            })
        return partner

    def _find_or_create_product(self, product_id):
        """Sync a single product from PrestaShop"""
        xml = self._api_get('products', product_id)
        if xml is None: return None
        
        prod_data = xml.find('product')
        sku = prod_data.find('reference').text or f"PS_{product_id}"
        name = prod_data.find('name/language[@id="1"]').text # Default to first language
        price_ht = float(prod_data.find('price').text or 0.0)
        
        product = self.env['product.product'].search([('default_code', '=', sku)], limit=1)
        if not product:
            product = self.env['product.product'].create({
                'name': name,
                'default_code': sku,
                'list_price': price_ht,
                'type': 'consu', # Consumable by default
            })
        return product

    def action_fetch_orders_preview(self):
        """Fetch orders from PrestaShop and store in preview table"""
        self.ensure_one()
        _logger.info("Fetching PrestaShop orders for preview: %s", self.name)

        from datetime import datetime, timedelta

        # Determine date filter (last 30 days by default)
        date_filter = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

        # Fetch orders
        params = {
            'display': 'full',
            'filter[date_add]': f"[{date_filter},%]"
        }
        xml = self._api_get('orders', params=params)
        if xml is None:
            raise UserError(_("Failed to connect to PrestaShop API. Check credentials."))

        orders = xml.findall('.//order')
        new_count = 0
        updated_count = 0

        for ord_data in orders:
            ps_order_id = ord_data.find('id').text

            # Check if already in preview
            existing_preview = self.env['prestashop.order.preview'].search([
                ('prestashop_order_id', '=', ps_order_id),
                ('instance_id', '=', self.id)
            ], limit=1)

            # Skip if already imported
            if existing_preview and existing_preview.state == 'imported':
                continue

            # Extract order data
            customer_id = ord_data.find('id_customer').text
            order_ref = ord_data.find('reference').text or f"PS-{ps_order_id}"
            order_date_str = ord_data.find('date_add').text
            order_date = fields.Datetime.to_datetime(order_date_str) if order_date_str else False

            total_paid = float(ord_data.find('total_paid').text or 0.0)
            payment_method = ord_data.find('payment').text or 'Unknown'
            order_state_id = ord_data.find('current_state').text

            # Get customer details
            customer_xml = self._api_get('customers', customer_id)
            if customer_xml is not None:
                cust_data = customer_xml.find('customer')
                customer_email = cust_data.find('email').text
                first_name = cust_data.find('firstname').text or ''
                last_name = cust_data.find('lastname').text or ''
                customer_name = f"{first_name} {last_name}".strip()
            else:
                customer_email = 'unknown@example.com'
                customer_name = 'Unknown Customer'

            # Get order lines
            lines_xml = ord_data.findall('.//order_row')
            line_vals = []

            for line in lines_xml:
                ps_prod_id = line.find('product_id').text
                prod_name = line.find('product_name').text or 'Unknown Product'
                prod_ref = line.find('product_reference').text or ''
                qty = float(line.find('product_quantity').text or 0.0)
                unit_price = float(line.find('unit_price_tax_excl').text or 0.0)
                total = float(line.find('total_price_tax_excl').text or 0.0)

                line_vals.append((0, 0, {
                    'prestashop_product_id': ps_prod_id,
                    'product_name': prod_name,
                    'product_reference': prod_ref,
                    'quantity': qty,
                    'unit_price': unit_price,
                    'total_price': total,
                }))

            # Create or update preview
            preview_vals = {
                'name': order_ref,
                'instance_id': self.id,
                'prestashop_order_id': ps_order_id,
                'prestashop_order_date': order_date,
                'customer_name': customer_name,
                'customer_email': customer_email,
                'prestashop_customer_id': customer_id,
                'total_amount': total_paid,
                'payment_method': payment_method,
                'order_state': order_state_id,
                'raw_data': ET.tostring(ord_data, encoding='unicode'),
                'line_ids': [(5, 0, 0)] + line_vals,  # Clear existing lines and add new
                'state': 'pending',
            }

            if existing_preview:
                existing_preview.write(preview_vals)
                updated_count += 1
            else:
                self.env['prestashop.order.preview'].create(preview_vals)
                new_count += 1

        # Show result notification
        message = _("Fetched %d new orders, updated %d orders") % (new_count, updated_count)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Fetch Complete'),
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {
                    'type': 'ir.actions.act_window',
                    'res_model': 'prestashop.order.preview',
                    'view_mode': 'list,form',
                    'domain': [('instance_id', '=', self.id), ('state', '=', 'pending')],
                }
            }
        }

    def action_sync_now(self):
        """Main entry point for synchronization - AUTO MODE (backwards compatible)"""
        self.ensure_one()
        _logger.info("Starting PrestaShop Sync for %s (AUTO MODE)", self.name)

        # First fetch to preview
        self.action_fetch_orders_preview()

        # Then auto-import all pending
        pending_previews = self.env['prestashop.order.preview'].search([
            ('instance_id', '=', self.id),
            ('state', '=', 'pending')
        ])

        for preview in pending_previews:
            try:
                preview._import_to_odoo()
            except Exception as e:
                _logger.error("Auto-import failed for order %s: %s",
                             preview.prestashop_order_id, str(e))
                continue

        self.last_sync_date = fields.Datetime.now()
        return True

class PrestaShopSyncLog(models.Model):
    _name = 'prestashop.sync.log'
    _description = 'PrestaShop Sync Log'
    _order = 'create_date desc'

    instance_id = fields.Many2one('prestashop.instance', string='Instance', ondelete='cascade')
    name = fields.Char(string='Operation')
    status = fields.Selection([('success', 'Success'), ('error', 'Error')], default='success')
    message = fields.Text()
    raw_data = fields.Text(string='Raw Data')
