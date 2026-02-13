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
    auto_sync = fields.Boolean(string='Hourly Auto-Sync', default=False)
    
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True)

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

    def action_sync_now(self):
        """Main entry point for synchronization"""
        self.ensure_one()
        _logger.info("Starting PrestaShop Sync for %s", self.name)
        
        # 1. Determine filter date (Last 2 months if first sync, else last sync date)
        from datetime import datetime, timedelta
        if not self.last_sync_date:
            date_filter = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        else:
            date_filter = self.last_sync_date.strftime('%Y-%m-%d %H:%M:%S')

        # 2. Fetch orders
        params = {
            'display': 'full',
            'filter[date_add]': f"[{date_filter},%]"
        }
        xml = self._api_get('orders', params=params)
        if xml is None: return False
        
        orders = xml.findall('.//order')
        for ord_data in orders:
            ps_order_id = ord_data.find('id').text
            
            # Check if exists
            existing = self.env['sale.order'].search([
                ('prestashop_order_id', '=', ps_order_id),
                ('prestashop_instance_id', '=', self.id)
            ], limit=1)
            if existing: continue
            
            try:
                # Sync Customer
                customer_id = ord_data.find('id_customer').text
                partner = self._find_or_create_customer(customer_id)
                
                # Create Sale Order
                so_vals = {
                    'partner_id': partner.id,
                    'prestashop_instance_id': self.id,
                    'prestashop_order_id': ps_order_id,
                    'prestashop_source': 'prestashop',
                    'warehouse_id': self.warehouse_id.id,
                    'company_id': self.company_id.id,
                    'origin': f"PS#{ps_order_id}",
                }
                order = self.env['sale.order'].create(so_vals)
                
                # Sync Order Lines
                lines_xml = ord_data.findall('.//order_row')
                for line in lines_xml:
                    ps_prod_id = line.find('product_id').text
                    product = self._find_or_create_product(ps_prod_id)
                    qty = float(line.find('product_quantity').text or 0.0)
                    price = float(line.find('unit_price_tax_excl').text or 0.0)
                    
                    self.env['sale.order.line'].create({
                        'order_id': order.id,
                        'product_id': product.id,
                        'product_uom_qty': qty,
                        'price_unit': price,
                    })
                
                _logger.info("Successfully synced PrestaShop Order %s", ps_order_id)
            except Exception as e:
                _logger.error("Error syncing PrestaShop Order %s: %s", ps_order_id, str(e))
                self.env['prestashop.sync.log'].create({
                    'instance_id': self.id,
                    'name': f"Order {ps_order_id}",
                    'status': 'error',
                    'message': str(e),
                })
        
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
