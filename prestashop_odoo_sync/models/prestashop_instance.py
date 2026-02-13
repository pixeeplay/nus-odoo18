import logging
from odoo import models, fields

_logger = logging.getLogger(__name__)

class PrestaShopInstance(models.Model):
    _name = 'prestashop.instance'
    _description = 'PrestaShop Instance'

    name = fields.Char(required=True)
    url = fields.Char(string='Store URL', required=True)
    api_key = fields.Char(string='API Key', required=True)
    active = fields.Boolean(default=True)
