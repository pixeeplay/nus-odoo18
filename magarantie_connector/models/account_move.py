# -*- coding: utf-8 -*-
from odoo import models, fields


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    magarantie_sale_id = fields.Many2one(
        'magarantie.sale',
        string='Warranty Sale',
    )
    is_magarantie_warranty = fields.Boolean(
        string='Is Warranty Line',
        default=False,
    )
