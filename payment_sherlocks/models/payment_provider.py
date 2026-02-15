import hashlib
import hmac
import logging

from odoo import fields, models

from odoo.addons.payment_sherlocks import const

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('sherlocks', "Sherlocks")],
        ondelete={'sherlocks': 'set default'},
    )
    sherlocks_merchant_id = fields.Char(
        string="Merchant ID",
        help="The 15-digit merchant identifier provided by LCL/Worldline.",
        required_if_provider='sherlocks',
        groups='base.group_user',
    )
    sherlocks_secret_key = fields.Char(
        string="Secret Key",
        help="The HMAC-SHA-256 secret key provided by LCL/Worldline.",
        required_if_provider='sherlocks',
        groups='base.group_user',
    )
    sherlocks_key_version = fields.Char(
        string="Key Version",
        help="The version of the secret key (starts at 1).",
        required_if_provider='sherlocks',
        default='1',
        groups='base.group_user',
    )

    def _get_default_payment_method_codes(self):
        """ Override to return the default payment method codes. """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'sherlocks':
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

    def _sherlocks_get_api_url(self):
        """ Return the Sips Paypage POST URL based on the provider state. """
        if self.state == 'enabled':
            return const.PRODUCTION_URL
        return const.TEST_URL

    def _sherlocks_compute_seal(self, data_str):
        """ Compute the HMAC-SHA-256 seal for the given Data string.

        :param str data_str: The pipe-separated Data string to sign.
        :return: The hex-encoded HMAC-SHA-256 seal.
        :rtype: str
        """
        return hmac.new(
            self.sherlocks_secret_key.encode('utf-8'),
            data_str.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
