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
        """Return clean base URL for API calls, always ending with /api"""
        url = self.url.rstrip('/')
        if not url.endswith('/api'):
            url = url + '/api'
        return url

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

    # ---- Customer & Address sync ----

    def _find_or_create_customer(self, customer_id):
        """Find or create customer from PrestaShop with full details"""
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
            company_name = customer.get('company', '')
            siret = customer.get('siret', '')
            note = customer.get('note', '')

            vals = {
                'name': f"{firstname} {lastname}".strip() or f'Customer {customer_id}',
                'email': email,
                'comment': f'PrestaShop ID: {customer_id}',
                'customer_rank': 1,
            }

            if company_name:
                # Create company partner first
                company_partner = self.env['res.partner'].search([
                    ('name', '=', company_name),
                    ('is_company', '=', True),
                ], limit=1)
                if not company_partner:
                    company_vals = {
                        'name': company_name,
                        'is_company': True,
                        'customer_rank': 1,
                    }
                    if siret:
                        company_vals['company_registry'] = siret
                    company_partner = self.env['res.partner'].create(company_vals)
                vals['parent_id'] = company_partner.id

            if note:
                vals['comment'] = f'PrestaShop ID: {customer_id}\n{note}'

            partner = self.env['res.partner'].create(vals)
            _logger.info("Created customer %s from PrestaShop ID %s", partner.name, customer_id)
            return partner

        except Exception as e:
            _logger.error("Error creating customer %s: %s", customer_id, str(e))
            return None

    def _find_or_create_address(self, address_id, parent_partner, address_type):
        """Fetch address from PrestaShop and create/find partner address.
        address_type: 'invoice' or 'delivery'
        """
        self.ensure_one()
        try:
            if not address_id or str(address_id) == '0':
                return parent_partner

            data = self._api_get('addresses', str(address_id))
            addr = data.get('address', {})

            firstname = addr.get('firstname', '')
            lastname = addr.get('lastname', '')
            company = addr.get('company', '')
            address1 = addr.get('address1', '')
            address2 = addr.get('address2', '')
            postcode = addr.get('postcode', '')
            city = addr.get('city', '')
            phone = addr.get('phone', '')
            phone_mobile = addr.get('phone_mobile', '')
            vat_number = addr.get('vat_number', '')

            # Get country
            country = False
            country_id_ps = str(addr.get('id_country', ''))
            if country_id_ps and country_id_ps != '0':
                try:
                    country_data = self._api_get('countries', country_id_ps)
                    iso_code = country_data.get('country', {}).get('iso_code', '')
                    if iso_code:
                        country = self.env['res.country'].search([
                            ('code', '=', iso_code.upper())
                        ], limit=1)
                except Exception:
                    pass

            # Get state
            state = False
            state_id_ps = str(addr.get('id_state', ''))
            if state_id_ps and state_id_ps != '0':
                try:
                    state_data = self._api_get('states', state_id_ps)
                    state_iso = state_data.get('state', {}).get('iso_code', '')
                    if state_iso and country:
                        state = self.env['res.country.state'].search([
                            ('code', '=', state_iso),
                            ('country_id', '=', country.id),
                        ], limit=1)
                except Exception:
                    pass

            addr_name = f"{firstname} {lastname}".strip()
            if company:
                addr_name = f"{company} - {addr_name}" if addr_name else company

            # Check if address already exists
            existing = self.env['res.partner'].search([
                ('parent_id', '=', parent_partner.id),
                ('type', '=', address_type),
                ('street', '=', address1),
                ('zip', '=', postcode),
            ], limit=1)

            if existing:
                return existing

            vals = {
                'parent_id': parent_partner.id,
                'type': address_type,
                'name': addr_name or parent_partner.name,
                'street': address1,
                'street2': address2 or False,
                'zip': postcode,
                'city': city,
                'phone': phone or False,
                'mobile': phone_mobile or False,
            }

            if company:
                vals['company_name'] = company

            if country:
                vals['country_id'] = country.id
            if state:
                vals['state_id'] = state.id
            if vat_number:
                vals['vat'] = vat_number

            address_partner = self.env['res.partner'].create(vals)
            _logger.info("Created %s address for %s", address_type, parent_partner.name)
            return address_partner

        except Exception as e:
            _logger.error("Error creating address %s: %s", address_id, str(e))
            return parent_partner

    # ---- Product sync ----

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
                'type': 'consu',
                'list_price': 0.0,
                'description_sale': f'PrestaShop Product ID: {product_id}',
            })
            _logger.info("Created product %s from PrestaShop ID %s", product.name, product_id)

        return product

    def _get_shipping_product(self):
        """Get or create a service product for shipping costs"""
        product = self.env.ref('prestashop_odoo_sync.product_shipping', raise_if_not_found=False)
        if not product:
            product = self.env['product.product'].search([
                ('default_code', '=', 'PS-SHIPPING')
            ], limit=1)
        if not product:
            product = self.env['product.product'].create({
                'name': 'Frais de port (PrestaShop)',
                'default_code': 'PS-SHIPPING',
                'type': 'service',
                'list_price': 0.0,
                'taxes_id': [(5, 0, 0)],
            })
        return product

    # ---- Carrier & Status sync ----

    def _get_carrier_name(self, carrier_id):
        """Fetch carrier name from PrestaShop"""
        try:
            if not carrier_id or str(carrier_id) == '0':
                return ''
            data = self._api_get('carriers', str(carrier_id))
            carrier = data.get('carrier', {})
            return carrier.get('name', '') or ''
        except Exception:
            return ''

    def _get_order_status_name(self, status_id):
        """Fetch order status name from PrestaShop"""
        try:
            if not status_id or str(status_id) == '0':
                return ''
            data = self._api_get('order_histories', params={
                'filter[id_order_state]': str(status_id),
            })
            # Try direct order_states endpoint
            try:
                data = self._api_get('order_states', str(status_id))
                state = data.get('order_state', {})
                name = state.get('name', '')
                if isinstance(name, dict):
                    return name.get('1', '') or name.get(list(name.keys())[0], '') if name else ''
                return name or ''
            except Exception:
                return ''
        except Exception:
            return ''

    def _get_delivery_country(self, address_id):
        """Get delivery country name from address"""
        try:
            if not address_id or str(address_id) == '0':
                return ''
            data = self._api_get('addresses', str(address_id))
            addr = data.get('address', {})
            country_id_ps = str(addr.get('id_country', ''))
            if country_id_ps and country_id_ps != '0':
                country_data = self._api_get('countries', country_id_ps)
                country = country_data.get('country', {})
                name = country.get('name', '')
                if isinstance(name, dict):
                    return name.get('1', '') or name.get(list(name.keys())[0], '') if name else ''
                return name or ''
            return ''
        except Exception:
            return ''

    def _is_new_customer(self, customer_id):
        """Check if customer already had orders before"""
        existing = self.env['res.partner'].search([
            ('comment', 'ilike', f'PrestaShop ID: {customer_id}')
        ], limit=1)
        return not bool(existing)

    # ---- Order sync ----

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
                id_address_delivery = str(order.get('id_address_delivery', ''))
                id_address_invoice = str(order.get('id_address_invoice', ''))
                id_carrier = str(order.get('id_carrier', ''))
                current_state = str(order.get('current_state', ''))
                payment_method = order.get('payment', '')
                total_shipping = float(order.get('total_shipping_tax_excl', 0) or order.get('total_shipping', 0) or 0)

                # Check if new customer before creating
                is_new = self._is_new_customer(customer_id)

                # Find or create customer
                partner = self._find_or_create_customer(customer_id)
                if not partner:
                    _logger.warning("Could not create customer for order %s", order_id)
                    continue

                # Find or create addresses
                invoice_partner = self._find_or_create_address(id_address_invoice, partner, 'invoice')
                delivery_partner = self._find_or_create_address(id_address_delivery, partner, 'delivery')

                # Get carrier name and order status
                carrier_name = self._get_carrier_name(id_carrier)
                status_name = self._get_order_status_name(current_state)
                delivery_country = self._get_delivery_country(id_address_delivery)

                sale_order = self.env['sale.order'].create({
                    'partner_id': partner.id,
                    'partner_invoice_id': invoice_partner.id,
                    'partner_shipping_id': delivery_partner.id,
                    'date_order': date_add,
                    'warehouse_id': self.warehouse_id.id,
                    'company_id': self.company_id.id,
                    'prestashop_instance_id': self.id,
                    'prestashop_order_id': order_id,
                    'prestashop_reference': order_reference,
                    'prestashop_source': 'prestashop',
                    'prestashop_carrier': carrier_name,
                    'prestashop_payment': payment_method,
                    'prestashop_status': status_name,
                    'prestashop_delivery_country': delivery_country,
                    'prestashop_new_customer': is_new,
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
                    ecotax = float(row.get('product_ean13_ecotax', 0) or row.get('ecotax', 0) or 0)

                    product = self._find_or_create_product(product_id, product_name, product_reference)

                    self.env['sale.order.line'].create({
                        'order_id': sale_order.id,
                        'product_id': product.id,
                        'name': product_name,
                        'product_uom_qty': quantity,
                        'price_unit': unit_price,
                        'prestashop_ecotax': ecotax,
                    })

                # Add shipping cost line if > 0
                if total_shipping > 0:
                    shipping_product = self._get_shipping_product()
                    self.env['sale.order.line'].create({
                        'order_id': sale_order.id,
                        'product_id': shipping_product.id,
                        'name': f'Frais de port - {carrier_name}' if carrier_name else 'Frais de port',
                        'product_uom_qty': 1,
                        'price_unit': total_shipping,
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

    @api.model
    def _cron_sync_orders(self):
        """Cron method: sync orders for all active instances"""
        instances = self.search([('active', '=', True)])
        for instance in instances:
            try:
                instance.action_sync_orders()
                _logger.info("Cron sync completed for instance %s", instance.name)
            except Exception as e:
                _logger.error("Cron sync failed for instance %s: %s", instance.name, str(e))
