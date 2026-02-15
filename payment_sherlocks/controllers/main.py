import logging
import pprint

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class SherlocksController(http.Controller):
    _return_url = '/payment/sherlocks/return'
    _webhook_url = '/payment/sherlocks/webhook'

    @http.route(
        _return_url, type='http', auth='public', methods=['POST'],
        csrf=False, save_session=False,
    )
    def sherlocks_return_from_checkout(self, **data):
        """ Handle the return from the Sips payment page (browser redirect).

        The bank POSTs Data, Seal, and InterfaceVersion to this URL after the
        customer completes (or cancels) the payment on the hosted page.
        """
        _logger.info(
            "Handling return from Sherlocks with data:\n%s", pprint.pformat(data)
        )
        request.env['payment.transaction'].sudo()._handle_notification_data(
            'sherlocks', data
        )
        return request.redirect('/payment/status')

    @http.route(
        _webhook_url, type='http', auth='public', methods=['POST'],
        csrf=False,
    )
    def sherlocks_webhook(self, **data):
        """ Handle the server-to-server notification from Sips.

        The bank server POSTs the same Data, Seal, InterfaceVersion fields
        asynchronously to confirm the transaction result.
        """
        _logger.info(
            "Notification received from Sherlocks with data:\n%s",
            pprint.pformat(data),
        )
        try:
            request.env['payment.transaction'].sudo()._handle_notification_data(
                'sherlocks', data
            )
        except Exception:
            _logger.exception(
                "Unable to handle the notification data; skipping to acknowledge."
            )
        return request.make_response('')
