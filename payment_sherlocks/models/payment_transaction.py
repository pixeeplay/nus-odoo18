import hmac
import logging

from odoo import _, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_sherlocks import const
from odoo.addons.payment_sherlocks.controllers.main import SherlocksController

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        """ Override to return Sherlocks/Sips-specific rendering values.

        The Sips Paypage POST flow requires a form with a `Data` field (pipe-separated
        key=value pairs), an `InterfaceVersion`, a `Seal` (HMAC-SHA-256), and a
        `SealAlgorithm` field. The form auto-submits to the Sips payment page.

        :param dict processing_values: The generic processing values of the transaction.
        :return: The dict of provider-specific rendering values.
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'sherlocks':
            return res

        base_url = self.provider_id.get_base_url()
        currency_code = const.CURRENCY_CODES.get(self.currency_id.name, '978')

        # Build the Data parameters (will be sorted alphabetically and pipe-separated)
        data_params = {
            'amount': str(int(self.amount * 100)),
            'automaticResponseUrl': f'{base_url}{SherlocksController._webhook_url}',
            'currencyCode': currency_code,
            'keyVersion': self.provider_id.sherlocks_key_version or '1',
            'merchantId': self.provider_id.sherlocks_merchant_id,
            'normalReturnUrl': f'{base_url}{SherlocksController._return_url}',
            'orderChannel': 'INTERNET',
            'transactionReference': self.reference,
        }

        # Create the pipe-separated Data string (keys sorted alphabetically)
        data_str = '|'.join(f'{k}={v}' for k, v in sorted(data_params.items()))

        # Compute the HMAC-SHA-256 seal
        seal = self.provider_id._sherlocks_compute_seal(data_str)

        return {
            'api_url': self.provider_id._sherlocks_get_api_url(),
            'Data': data_str,
            'InterfaceVersion': const.INTERFACE_VERSION,
            'Seal': seal,
            'SealAlgorithm': 'HMAC-SHA-256',
        }

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ Override to find the transaction based on Sips notification data.

        :param str provider_code: The code of the provider that handled the transaction.
        :param dict notification_data: The notification data (Data, Seal, InterfaceVersion).
        :return: The transaction matching the notification data.
        :rtype: payment.transaction
        :raise ValidationError: If the data doesn't match any transaction.
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'sherlocks' or len(tx) == 1:
            return tx

        # Parse the Data field to extract the transaction reference
        data_str = notification_data.get('Data', '')
        data_dict = dict(
            item.split('=', 1) for item in data_str.split('|') if '=' in item
        )
        reference = data_dict.get('transactionReference')

        if not reference:
            raise ValidationError(
                "Sherlocks: " + _("Received data with missing transaction reference.")
            )

        tx = self.search([
            ('reference', '=', reference),
            ('provider_code', '=', 'sherlocks'),
        ])
        if not tx:
            raise ValidationError(
                "Sherlocks: " + _(
                    "No transaction found matching reference %s.", reference
                )
            )
        return tx

    def _process_notification_data(self, notification_data):
        """ Override to process the notification data sent by Sips.

        Verifies the HMAC-SHA-256 seal, parses the response code, and updates the
        transaction status accordingly.

        :param dict notification_data: The notification data (Data, Seal, InterfaceVersion).
        :raise ValidationError: If the seal verification fails.
        """
        super()._process_notification_data(notification_data)
        if self.provider_code != 'sherlocks':
            return

        data_str = notification_data.get('Data', '')
        seal = notification_data.get('Seal', '')

        # Verify the seal
        expected_seal = self.provider_id._sherlocks_compute_seal(data_str)
        if not hmac.compare_digest(seal.lower(), expected_seal.lower()):
            raise ValidationError(
                "Sherlocks: " + _("Received data with invalid seal.")
            )

        # Parse the Data string
        data_dict = dict(
            item.split('=', 1) for item in data_str.split('|') if '=' in item
        )

        response_code = data_dict.get('responseCode', '')
        self.provider_reference = data_dict.get('transactionId', '')

        if response_code in const.RESPONSE_CODES_MAPPING['done']:
            self._set_done()
        elif response_code in const.RESPONSE_CODES_MAPPING['pending']:
            self._set_pending()
        elif response_code in const.RESPONSE_CODES_MAPPING['cancel']:
            self._set_canceled()
        else:
            _logger.warning(
                "Received data with unexpected response code (%s) for transaction "
                "with reference %s.",
                response_code,
                self.reference,
            )
            self._set_error(
                "Sherlocks: " + _(
                    "Received unexpected response code: %s", response_code
                )
            )
