# -*- coding: utf-8 -*-

from odoo.addons.payment.controllers import portal as payment_portal
from odoo.http import request
from odoo.exceptions import UserError, ValidationError
from odoo import _


class PaymentPortal(payment_portal.PaymentPortal):

    def _get_extra_payment_form_values(self, invoice_id=None, access_token=None, **kwargs):
        rendering_context = super()._get_extra_payment_form_values(
            invoice_id=invoice_id, access_token=access_token, **kwargs)
        if invoice_id:
            amount = request.env['account.move'].sudo().browse(invoice_id).amount_residual
            rendering_context.update({
                'alma_eligibility': request.env['payment.provider']._alma_get_eligibility(amount),
                'alma_plan': request.env['payment.provider']._alma_get_available_plan()
            })
        else:
            # TODO get real amount
            rendering_context.update({
                'alma_eligibility': request.env['payment.provider'].sudo()._alma_get_eligibility(1000),
                'alma_plan': request.env['payment.provider'].sudo()._alma_get_available_plan()
            })
        return rendering_context

    def _create_transaction(self, *args, invoice_id=None, custom_create_values=None, **kwargs):
        """ Override of `payment` to add the installments count in the custom create values.
        """
        if 'alma_options' in kwargs:
            if custom_create_values is None:
                custom_create_values = {}
            if not kwargs.get('alma_options'):
                raise UserError(_("Alma payment is not selectable."))
            custom_create_values['installments_count'] = int(eval(kwargs.get('alma_options'))[0])
            custom_create_values['deferred_months'] = int(eval(kwargs.get('alma_options'))[1])
            custom_create_values['deferred_days'] = int(eval(kwargs.get('alma_options'))[2])
            kwargs.pop('alma_options')
        return super()._create_transaction(
            *args, invoice_id=invoice_id, custom_create_values=custom_create_values, **kwargs
        )

    @staticmethod
    def _validate_transaction_kwargs(kwargs, additional_allowed_keys=()):
        whitelist = {
            'provider_id',
            'payment_method_id',
            'token_id',
            'amount',
            'flow',
            'tokenization_requested',
            'landing_route',
            'is_validation',
            'csrf_token',
            'alma_options',
        }
        whitelist.update(additional_allowed_keys)
        rejected_keys = set(kwargs.keys()) - whitelist
        if rejected_keys:
            raise ValidationError(
                _("The following kwargs are not whitelisted: %s", ', '.join(rejected_keys))
            )
