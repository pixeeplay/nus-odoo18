# coding: utf-8

import logging
import requests
import json

from werkzeug import urls

from odoo import _, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.payment_alma.controllers.main import AlmaController
from odoo.addons.payment_alma.const import PAYMENT_STATUS_MAPPING
from odoo.addons.payment import utils as payment_utils


_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    installments_count = fields.Integer(
        string='Installments count',
        default=1)
    deferred_days = fields.Integer(
        string='Defferd days',
        default=0)
    deferred_months = fields.Integer(
        string='Deffered months',
        default=0)

    def _get_specific_rendering_values(self, processing_values):
        """ Override of payment to return Alma-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific processing values.
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'alma':
            return res

        # Initiate the payment and retrieve the payment link data.
        converted_amount = self.amount * 100
        base_url = self.provider_id.get_base_url()
        if self.partner_lang:
            iso_lang_code = self.env['res.lang'].search([('code', '=', self.partner_lang)]).iso_code
            if iso_lang_code in ('fr_BE', 'fr_CA', 'fr_CH'):
                iso_lang_code == 'fr'
        else:
            iso_lang_code = 'en'
        first_name, last_name = payment_utils.split_partner_name(self.partner_name)
        data = {
            'payment': {
                'origin': 'online',
                'purchase_amount': converted_amount,
                'return_url': urls.url_join(base_url, AlmaController._return_url),
                'customer_cancel_url': urls.url_join(base_url, AlmaController._cancel_url),
                'billing_address': {
                    'first_name': first_name or '',
                    'last_name': last_name or '',
                    'line1': self.partner_address or '',
                    'postal_code': self.partner_zip or '',
                    'city': self.partner_city or '',
                    'country': self.partner_country_id.name or '',
                    'phone': self.partner_phone or '',
                },
                'locale': iso_lang_code if iso_lang_code in ('fr', 'en', 'it', 'es', 'de', 'nl', 'nl_BE') else 'en',
                'installments_count': self.installments_count,
                'deferred_months': self.deferred_months,
                'deferred_days': self.deferred_days,
                'expires_after': 10,
                'failure_return_url': urls.url_join(base_url, AlmaController._failure_return_url),
                'ipn_callback_url': urls.url_join(base_url, AlmaController._webhook_url),
            },
            'customer': {
                'first_name': first_name or '',
                'last_name': last_name or '',
                'email': self.partner_email or '',
                'phone': self.partner_phone or '',
            },
            'order': {
                'merchant_reference': self.reference,
            },
        }
        response_data = requests.request(
            "POST",
            (self.provider_id._alma_get_api_url() + '/v1/payments'),
            headers=self.provider_id._alma_get_headers(),
            data=json.dumps(data)
        )
        if response_data.status_code == 200:
            # Extract the payment link URL and embed it in the redirect form.
            rendering_values = {'api_url': response_data.json()['url']}
            self.provider_reference = response_data.json()['id']
            return rendering_values
        else:
            raise ValidationError("Alma: " + _("Something wrong with the alma api, please retry later"))

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ Override of payment to find the transaction based on Alma data.

        :param str provider_code: The code of the provider that handled the transaction.
        :param dict notification_data: The notification data sent by the provider.
        :return: The transaction if found.
        :rtype: recordset of `payment.transaction`
        :raise ValidationError: If inconsistent data were received.
        :raise ValidationError: If the data match no transaction.
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'alma' or len(tx) == 1:
            return tx
        provider_reference = notification_data.get('pid')
        if not provider_reference:
            raise ValidationError("Alma: " + _("Received data with missing reference."))

        tx = self.search([('provider_reference', '=', provider_reference), ('provider_code', '=', 'alma')])
        if not tx:
            raise ValidationError(
                "Alma: " + _("No transaction found matching provider reference %s.", provider_reference)
            )
        return tx

    def _process_notification_data(self, notification_data):
        """ Override of payment to process the transaction based on Flutterwave data.

        Note: self.ensure_one()

        :param dict notification_data: The notification data sent by the provider.
        :return: None
        :raise ValidationError: If inconsistent data were received.
        """
        super()._process_notification_data(notification_data)
        if self.provider_code != 'alma':
            return

        if not notification_data.get('pid'):
            raise ValidationError('ALMA: The provider reference is missing')

        if self.provider_reference != notification_data['pid']:
            raise ValidationError('ALMA: bad provider reference')

        # Verify the notification data.
        verification_response_content = requests.request(
            "GET",
            (self.provider_id._alma_get_api_url() + '/v1/payments/' + self.provider_reference),
            headers=self.provider_id._alma_get_headers(),
        )
        # Process the verified notification data.
        payment_status = verification_response_content.json()['state'].lower()
        if payment_status in PAYMENT_STATUS_MAPPING['pending']:
            self._set_pending()
        elif payment_status in PAYMENT_STATUS_MAPPING['done']:
            self._set_done()
        elif payment_status in PAYMENT_STATUS_MAPPING['cancel']:
            self._set_canceled()
        elif payment_status in PAYMENT_STATUS_MAPPING['error']:
            self._set_error(_(
                "An error occurred during the processing of your payment (status %s). Please try "
                "again.", payment_status
            ))
        else:
            _logger.warning(
                "Received data with invalid payment status (%s) for transaction with reference %s.",
                payment_status, self.reference
            )
            self._set_error("Alma: " + _("Unknown payment status: %s", payment_status))

    def _send_refund_request(self, amount_to_refund=None):
        """
        Note: self.ensure_one()
        :param float amount_to_refund: The amount to refund
        :return: The refund transaction created to process the refund request.
        :rtype: recordset of `payment.transaction`
        """
        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)
        if self.provider_code != 'alma':
            return refund_tx

        converted_amount = -refund_tx.amount * 100
        data = {
            'amount': converted_amount,
            'merchant_reference': refund_tx.reference,
        }
        response_content = requests.request(
            "POST",
            (self.provider_id._alma_get_api_url() + '/v1/payments/' + self.provider_reference + '/refunds'),
            headers=self.provider_id._alma_get_headers(),
            data=json.dumps(data)
        )
        if response_content.status_code == 200:
            for refund in response_content.json()['refunds']:
                if refund['merchant_reference'] == refund_tx.reference:
                    _logger.info("ALMA: refund request accepted for transaction with reference %s", self.reference)
                    refund_tx.provider_reference = refund['id']
                    refund_tx._set_done()
                    self.env.ref('payment.cron_post_process_payment_tx')._trigger()
                    return refund_tx
        _logger.info("ALMA: refund request refused for transaction with reference %s", self.reference)
        refund_tx._set_canceled()
        return refund_tx
