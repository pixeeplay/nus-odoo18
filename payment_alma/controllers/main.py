# -*- coding: utf-8 -*-

import logging
import pprint

from odoo import http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class AlmaController(http.Controller):
    _return_url = '/payment/alma/return'
    _webhook_url = '/payment/alma/webhook'
    _cancel_url = '/payment/alma/cancel'
    _failure_return_url = '/payment/alma/failure'

    @http.route(_return_url, type='http', methods=['GET'], auth='public')
    def alma_return_from_checkout(self, **data):
        """ Process the notification data sent by Alma after redirection from checkout.

        :param dict data: The notification data.
        """
        _logger.info("Handling redirection from Alma with data:\n%s", pprint.pformat(data))
        request.env['payment.transaction'].sudo()._handle_notification_data('alma', data)
        return request.redirect('/payment/status')

    @http.route(_webhook_url, type='http', methods=['GET'], auth='public')
    def alma_return_from_ipn_callback(self, **data):
        try:
            request.env['payment.transaction'].sudo()._handle_notification_data('alma', data)
        except ValidationError:  # Acknowledge the notification to avoid getting spammed
            _logger.exception("ALMA: unable to handle the notification data; skipping to acknowledge")
        return 'SUCCESS'

    @http.route(_cancel_url, type='http', methods=['GET'], auth='public')
    def alma_return_customer_cancel(self, **data):
        _logger.info("Handling redirection from Alma with cancel customer")
        request.env['payment.transaction'].sudo()._handle_notification_data('alma', data)
        return request.redirect('/payment/status')

    @http.route(_failure_return_url, type='http', methods=['GET'], auth='public')
    def alma_return_failure(self, **data):
        _logger.info("Handling redirection from Alma with failure")
        request.env['payment.transaction'].sudo()._handle_notification_data('alma', data)
        return request.redirect('/payment/status')


class WebsiteSaleAlma(WebsiteSale):

    def _get_shop_payment_values(self, order, **kwargs):
        render_values = super(WebsiteSaleAlma, self)._get_shop_payment_values(order, **kwargs)
        render_values.update({
            'alma_eligibility': request.env['payment.provider'].sudo()._alma_get_eligibility(render_values['amount']),
            'alma_plan': request.env['payment.provider'].sudo()._alma_get_available_plan()
        })
        return render_values
