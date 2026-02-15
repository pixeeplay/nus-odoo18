# coding: utf-8

import logging
import requests
import json
from odoo import api, exceptions, fields, models, _
from odoo.addons.payment_alma import const

_logger = logging.getLogger(__name__)


class ProviderAlma(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('alma', 'Alma')],
        ondelete={'alma': 'set default'})
    alma_api_key = fields.Char(
        string='API key for production (Alma)',
        required_if_provider='alma',
        groups='base.group_user')
    alma_api_test_key = fields.Char(
        string='API key for test (Alma)',
        required_if_provider='alma',
        groups='base.group_user')
    # Payment options
    alma_1x = fields.Boolean(string="Payment 1X")
    alma_2x = fields.Boolean(string="Payment 2X")
    alma_3x = fields.Boolean(string="Payment 3X", default=True)
    alma_4x = fields.Boolean(string="Payment 4X", default=True)
    deferred_payment_days = fields.Boolean(string="Deferred payment d+15", default=False)
    deferred_payment_months = fields.Boolean(string="Deferred payment m+1", default=False)

    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'alma').update({
            'support_refund': 'partial',
        })

    def _alma_get_api_url(self):
        self.ensure_one()
        if self.state == 'enabled':
            return 'https://api.getalma.eu'
        else:
            return 'https://api.sandbox.getalma.eu'

    def _alma_get_headers(self):
        self.ensure_one()
        if self.code == 'alma':
            if self.state == 'enabled':
                key = self.alma_api_key
            elif self.state == 'test':
                key = self.alma_api_test_key
            else:
                raise exceptions.ValidationError(_('Alma provider is disabled'))
            return {
                'Authorization': "Alma-Auth " + key,
                'Content-type': 'application/json',
            }

    def _alma_get_available_plan(self):
        alma = self.get_alma_provider()
        alma_fee_plans = requests.request(
            "GET",
            (alma._alma_get_api_url() + '/v1/me/fee-plans'),
            headers=alma._alma_get_headers(),
        )
        if alma_fee_plans.status_code == 200:
            return alma_fee_plans.json()
        else:
            _logger.warning('ALMA: error when trying to get fee plan')
            return []

    def _alma_get_eligibility(self, amount):
        alma = self.get_alma_provider()
        data = {"purchase_amount": amount * 100, "queries": []}
        if alma.alma_1x:
            data['queries'].append({
                'installments_count': 1,
                'deferred_days': 0,
                'deferred_months': 0,
            })
            if alma.deferred_payment_days:
                data['queries'].append({
                'installments_count': 1,
                'deferred_days': 15,
                'deferred_months': 0,
            })
            if alma.deferred_payment_months:
                data['queries'].append({
                'installments_count': 1,
                'deferred_days': 0,
                'deferred_months': 1,
            })
        if alma.alma_2x:
            data['queries'].append({'installments_count': 2})
        if alma.alma_3x:
            data['queries'].append({'installments_count': 3})
        if alma.alma_4x:
            data['queries'].append({'installments_count': 4})
        response_data = requests.request(
            "POST",
            (alma._alma_get_api_url() + '/v2/payments/eligibility'),
            headers=alma._alma_get_headers(),
            data=json.dumps(data)
        )
        if response_data.status_code == 200:
            return response_data.json()
        else:
            return {}

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'alma':
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

    @api.model
    def get_alma_provider(self):
        alma = self.sudo().search([
            ('code', '=', 'alma'),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', self.env.company.id)
        ], limit=1)
        if not alma:
            raise exceptions.ValidationError(_('Alma provider is missing'))
        return alma
