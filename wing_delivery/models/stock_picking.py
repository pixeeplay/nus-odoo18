import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .wing_api import WingAPIError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    wing_order_id = fields.Char(
        'Wing Order ID', copy=False, readonly=True)
    wing_fulfillment_order_id = fields.Char(
        'Wing Fulfillment ID', copy=False, readonly=True)
    wing_wing_ref = fields.Char(
        'Wing Reference', copy=False, readonly=True)
    wing_fulfillment_status = fields.Selection([
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ], string='Wing Fulfillment Status', copy=False, readonly=True)
    wing_parcel_status = fields.Selection([
        ('pending', 'Pending'),
        ('labeled', 'Labeled'),
        ('shipped', 'Shipped'),
        ('in_transit', 'In Transit'),
        ('delivered', 'Delivered'),
    ], string='Wing Parcel Status', copy=False, readonly=True)

    def action_wing_refresh_status(self):
        """Manually refresh Wing tracking status from the API."""
        self.ensure_one()
        if not self.wing_fulfillment_order_id:
            raise UserError(_(
                "No Wing fulfillment order linked to this transfer."))
        if not self.carrier_id or self.carrier_id.delivery_type != 'wing':
            raise UserError(_(
                "This transfer does not use Wing as carrier."))
        api = self.carrier_id._get_wing_api()
        try:
            self.carrier_id._wing_update_one_picking(api, self)
        except WingAPIError as exc:
            raise UserError(_(
                "Wing status refresh failed: %s") % exc)
        finally:
            self.carrier_id._save_wing_tokens(api)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Wing Status Refreshed'),
                'message': _('Fulfillment: %s | Parcel: %s | Tracking: %s') % (
                    self.wing_fulfillment_status or '-',
                    self.wing_parcel_status or '-',
                    self.carrier_tracking_ref or '-'),
                'type': 'info',
                'sticky': False,
            },
        }
