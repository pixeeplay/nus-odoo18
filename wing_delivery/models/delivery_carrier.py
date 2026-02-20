import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .wing_api import WingAPI, WingAPIError

_logger = logging.getLogger(__name__)


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('wing', 'Wing')],
        ondelete={'wing': 'set default'},
    )

    # ── Wing credentials ──────────────────────────────────────────────
    wing_email = fields.Char('Wing Email')
    wing_password = fields.Char('Wing Password')
    wing_access_token = fields.Char('Access Token', copy=False)
    wing_refresh_token = fields.Char('Refresh Token', copy=False)
    wing_token_expires = fields.Char('Token Expires At', copy=False)

    # ── Wing configuration ────────────────────────────────────────────
    wing_pickup_id = fields.Char('Pickup Point ID')
    wing_pickup_name = fields.Char('Pickup Point Name')
    wing_default_weight = fields.Integer(
        'Default Weight (g)', default=500,
        help="Default parcel weight in grams when product weight is unknown.")
    wing_expeditors_json = fields.Text(
        'Available Carriers (JSON)', readonly=True)

    # ==================================================================
    #  Wing API helper
    # ==================================================================

    def _get_wing_api(self):
        """Return an authenticated WingAPI instance, saving refreshed tokens."""
        self.ensure_one()
        if not self.wing_email or not self.wing_password:
            raise UserError(_(
                "Please configure Wing credentials (email + password) "
                "on the delivery carrier."))
        api = WingAPI(
            email=self.wing_email,
            password=self.wing_password,
            access_token=self.wing_access_token or None,
            refresh_token=self.wing_refresh_token or None,
            token_expires_at=self.wing_token_expires or None,
        )
        return api

    def _save_wing_tokens(self, api):
        """Persist refreshed tokens back to the carrier record."""
        if api.token_changed:
            self.sudo().write({
                'wing_access_token': api.access_token,
                'wing_refresh_token': api.refresh_token,
                'wing_token_expires': api.token_expires_at or '',
            })

    # ==================================================================
    #  Action buttons (carrier form)
    # ==================================================================

    def action_wing_test_connection(self):
        """Test Wing API connection by fetching available carriers."""
        self.ensure_one()
        api = self._get_wing_api()
        try:
            expeditors = api.get_expeditors(limit=50)
        except WingAPIError as exc:
            raise UserError(_("Wing connection failed: %s") % exc)
        finally:
            self._save_wing_tokens(api)
        names = ', '.join(e.get('name', '?') for e in expeditors) or 'None'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Wing Connection OK'),
                'message': _('Found %d carrier(s): %s') % (
                    len(expeditors), names),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_wing_load_expeditors(self):
        """Fetch and store available Wing carriers as JSON."""
        self.ensure_one()
        api = self._get_wing_api()
        try:
            expeditors = api.get_expeditors(limit=100)
        except WingAPIError as exc:
            raise UserError(_("Failed to load carriers: %s") % exc)
        finally:
            self._save_wing_tokens(api)
        self.wing_expeditors_json = json.dumps(expeditors, indent=2)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Carriers Loaded'),
                'message': _('%d carrier(s) loaded.') % len(expeditors),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_wing_load_pickups(self):
        """Fetch pickup points and let user select one."""
        self.ensure_one()
        api = self._get_wing_api()
        try:
            pickups = api.get_pickups(limit=50)
        except WingAPIError as exc:
            raise UserError(_("Failed to load pickup points: %s") % exc)
        finally:
            self._save_wing_tokens(api)
        if not pickups:
            raise UserError(_("No pickup points found for your organization."))
        # Auto-select first active pickup
        active_pickups = [p for p in pickups if p.get('isActive')]
        if active_pickups:
            p = active_pickups[0]
            self.wing_pickup_id = p['id']
            addr = p.get('address', {})
            self.wing_pickup_name = '%s (%s, %s)' % (
                p.get('name', ''),
                addr.get('city', ''),
                addr.get('country', ''))
        info = '\n'.join(
            '- %s: %s, %s' % (
                p.get('name', '?'),
                p.get('address', {}).get('city', '?'),
                p.get('address', {}).get('country', '?'))
            for p in pickups)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Pickup Points'),
                'message': _('%d pickup(s) found. Selected: %s\n%s') % (
                    len(pickups), self.wing_pickup_name or '-', info),
                'type': 'info',
                'sticky': True,
            },
        }

    # ==================================================================
    #  Delivery carrier interface: required methods
    # ==================================================================

    def wing_rate_shipment(self, order):
        """Compute shipping rate.

        Wing does not expose a rating API, so we return 0 and rely on
        invoice_policy='real' to update the cost after shipment.
        """
        return {
            'success': True,
            'price': 0.0,
            'error_message': False,
            'warning_message': _(
                "Wing: shipping cost will be determined after shipment."),
        }

    def wing_send_shipping(self, pickings):
        """Send shipment to Wing and retrieve tracking + labels.

        Called by stock.picking.send_to_shipper() for each picking.
        Returns: list of {'exact_price': float, 'tracking_number': str}
        """
        results = []
        api = self._get_wing_api()
        try:
            for picking in pickings:
                result = self._wing_send_one(api, picking)
                results.append(result)
        finally:
            self._save_wing_tokens(api)
        return results

    def _wing_send_one(self, api, picking):
        """Process a single picking through Wing API."""
        partner = picking.partner_id
        if not partner:
            raise UserError(_(
                "Picking %s has no delivery address. "
                "Please set a partner before sending to Wing.") % picking.name)

        # ── Build customer data ───────────────────────────────────────
        customer = {
            'firstName': (partner.name or '').split(' ')[0],
            'lastName': ' '.join((partner.name or '').split(' ')[1:]) or '-',
            'email': partner.email or '',
            'phone': partner.phone or partner.mobile or '',
        }

        # ── Build shipping address ────────────────────────────────────
        shipping_address = {
            'street': partner.street or '',
            'city': partner.city or '',
            'zipCode': partner.zip or '',
            'country': (partner.country_id.code or 'FR').upper(),
        }
        if partner.street2:
            shipping_address['street'] += ', ' + partner.street2

        # ── Build products from move lines ────────────────────────────
        products = []
        for move in picking.move_ids.filtered(
                lambda m: m.state != 'cancel'):
            products.append({
                'id': str(move.product_id.id),
                'sku': move.product_id.default_code or str(move.product_id.id),
                'quantity': int(move.product_uom_qty),
                'price': move.product_id.list_price or 0.0,
            })

        if not products:
            raise UserError(_(
                "Picking %s has no products to ship.") % picking.name)

        # ── Step 1: Create Wing order ─────────────────────────────────
        _logger.info("Wing: creating order for picking %s", picking.name)
        try:
            order_data = api.create_order(
                reference=picking.name,
                customer=customer,
                shipping_address=shipping_address,
                products=products,
            )
        except WingAPIError as exc:
            raise UserError(_(
                "Wing order creation failed for %s: %s") % (
                picking.name, exc))

        wing_order_id = order_data.get('id', '')
        fulfillment_orders = order_data.get('fulfillmentOrders', [])
        wing_fo_id = fulfillment_orders[0]['id'] if fulfillment_orders else ''
        wing_ref = (fulfillment_orders[0].get('wingRef', '')
                    if fulfillment_orders else '')

        _logger.info(
            "Wing: order %s created → wing_order=%s, fo=%s",
            picking.name, wing_order_id, wing_fo_id)

        # ── Step 2: Create parcel ─────────────────────────────────────
        weight_grams = int((picking.shipping_weight or picking.weight or 0)
                           * 1000)
        if weight_grams <= 0:
            weight_grams = self.wing_default_weight or 500

        tracking_number = ''
        if wing_fo_id:
            try:
                fo_data = api.create_parcel(
                    fulfillment_order_id=wing_fo_id,
                    weight_grams=weight_grams,
                )
                parcels = fo_data.get('parcels', [])
                if parcels:
                    tracking_number = parcels[-1].get('trackingNumber', '')
            except WingAPIError as exc:
                _logger.warning(
                    "Wing: parcel creation failed for %s: %s",
                    picking.name, exc)

        # ── Step 3: Fetch full fulfillment for label/tracking ─────────
        if wing_fo_id and not tracking_number:
            try:
                fo_full = api.get_fulfillment_order(wing_fo_id)
                parcels = fo_full.get('parcels', [])
                if parcels:
                    tracking_number = parcels[-1].get('trackingNumber', '')
                # Try to get label URL
                invoice_url = fo_full.get('invoiceUrl')
                if invoice_url:
                    self._wing_attach_label(picking, invoice_url)
            except WingAPIError:
                pass

        # ── Step 4: Store Wing IDs on picking ─────────────────────────
        picking.write({
            'wing_order_id': wing_order_id,
            'wing_fulfillment_order_id': wing_fo_id,
            'wing_wing_ref': wing_ref,
            'wing_fulfillment_status': 'open',
            'wing_parcel_status': 'pending',
        })

        _logger.info(
            "Wing: picking %s → tracking=%s",
            picking.name, tracking_number or '(pending)')

        return {
            'exact_price': 0.0,
            'tracking_number': tracking_number or '',
        }

    def _wing_attach_label(self, picking, label_url):
        """Download a label PDF from URL and attach to picking."""
        import requests as req
        try:
            resp = req.get(label_url, timeout=30)
            resp.raise_for_status()
            filename = 'LabelShipping-wing-%s.pdf' % picking.name
            picking.message_post(
                body=_("Wing shipping label attached."),
                attachments=[(filename, resp.content)],
            )
        except Exception as exc:
            _logger.warning("Wing: failed to download label: %s", exc)

    def wing_get_tracking_link(self, picking):
        """Return a tracking URL for the customer portal."""
        if not picking.carrier_tracking_ref:
            return False
        return 'https://my.wing.eu/tracking/%s' % picking.carrier_tracking_ref

    def wing_cancel_shipment(self, pickings):
        """Cancel Wing shipment for the given pickings."""
        api = self._get_wing_api()
        try:
            for picking in pickings:
                fo_id = picking.wing_fulfillment_order_id
                if not fo_id:
                    continue
                try:
                    api.cancel_parcels([fo_id])
                    picking.write({
                        'wing_fulfillment_status': 'cancelled',
                        'wing_parcel_status': False,
                    })
                    picking.message_post(
                        body=_("Wing shipment cancelled."))
                    _logger.info(
                        "Wing: cancelled shipment for %s", picking.name)
                except WingAPIError as exc:
                    raise UserError(_(
                        "Wing cancellation failed for %s: %s") % (
                        picking.name, exc))
        finally:
            self._save_wing_tokens(api)

    # ==================================================================
    #  Return label support
    # ==================================================================

    def _compute_can_generate_return(self):
        super()._compute_can_generate_return()
        for carrier in self:
            if carrier.delivery_type == 'wing':
                carrier.can_generate_return = True

    def wing_get_return_label(self, picking, tracking_number=None,
                              origin_date=None):
        """Generate a return label via Wing."""
        fo_id = picking.wing_fulfillment_order_id
        if not fo_id:
            raise UserError(_(
                "No Wing fulfillment order for picking %s.") % picking.name)
        api = self._get_wing_api()
        try:
            api.create_return_parcel(
                fulfillment_order_id=fo_id,
                reason='CUSTOMER_REQUEST',
            )
            picking.message_post(
                body=_("Wing return parcel requested."))
        except WingAPIError as exc:
            raise UserError(_(
                "Wing return label failed: %s") % exc)
        finally:
            self._save_wing_tokens(api)

    # ==================================================================
    #  Cron: update tracking statuses
    # ==================================================================

    @api.model
    def _cron_wing_update_tracking(self):
        """Poll Wing API for tracking status updates on active pickings."""
        carriers = self.search([('delivery_type', '=', 'wing')])
        for carrier in carriers:
            pickings = self.env['stock.picking'].search([
                ('carrier_id', '=', carrier.id),
                ('wing_fulfillment_order_id', '!=', False),
                ('wing_fulfillment_status', 'not in',
                 ['delivered', 'cancelled', False]),
            ])
            if not pickings:
                continue

            api = carrier._get_wing_api()
            try:
                for picking in pickings:
                    carrier._wing_update_one_picking(api, picking)
            except Exception as exc:
                _logger.error("Wing cron tracking error: %s", exc)
            finally:
                carrier._save_wing_tokens(api)

    def _wing_update_one_picking(self, api, picking):
        """Update a single picking's Wing status from the API."""
        try:
            fo = api.get_fulfillment_order(
                picking.wing_fulfillment_order_id)
        except WingAPIError as exc:
            _logger.warning(
                "Wing: status check failed for %s: %s",
                picking.name, exc)
            return

        new_status = (fo.get('status', '') or '').lower().replace(
            '_', '_')  # OPEN → open, IN_PROGRESS → in_progress
        # Normalize common Wing statuses
        status_map = {
            'open': 'open',
            'in_progress': 'in_progress',
            'shipped': 'shipped',
            'delivered': 'delivered',
            'cancelled': 'cancelled',
        }
        mapped_status = status_map.get(new_status, new_status)

        parcels = fo.get('parcels', [])
        parcel_status = ''
        tracking = ''
        if parcels:
            last_parcel = parcels[-1]
            parcel_status = (last_parcel.get('status', '') or '').lower()
            tracking = last_parcel.get('trackingNumber', '')

        # Detect changes
        changes = []
        vals = {}
        old_fo_status = picking.wing_fulfillment_status or ''
        old_parcel_status = picking.wing_parcel_status or ''

        if mapped_status and mapped_status != old_fo_status:
            vals['wing_fulfillment_status'] = mapped_status
            changes.append(_("Fulfillment: %s → %s") % (
                old_fo_status, mapped_status))

        if parcel_status and parcel_status != old_parcel_status:
            # Map to known selection values
            parcel_map = {
                'pending': 'pending',
                'labeled': 'labeled',
                'shipped': 'shipped',
                'in_transit': 'in_transit',
                'delivered': 'delivered',
            }
            mapped_parcel = parcel_map.get(parcel_status, parcel_status)
            vals['wing_parcel_status'] = mapped_parcel
            changes.append(_("Parcel: %s → %s") % (
                old_parcel_status, mapped_parcel))

        if tracking and tracking != (picking.carrier_tracking_ref or ''):
            vals['carrier_tracking_ref'] = tracking
            changes.append(_("Tracking: %s") % tracking)

        wing_ref = fo.get('wingRef', '')
        if wing_ref and wing_ref != (picking.wing_wing_ref or ''):
            vals['wing_wing_ref'] = wing_ref

        if vals:
            picking.write(vals)
        if changes:
            picking.message_post(
                body=_("Wing status update: %s") % ', '.join(changes))
