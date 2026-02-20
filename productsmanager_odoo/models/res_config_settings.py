from odoo import api, fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pm_config_id = fields.Many2one(
        'pm.config', string='Products Manager Config',
        config_parameter='productsmanager_odoo.config_id',
    )
    pm_auto_sync = fields.Boolean(
        string='Auto Sync',
        config_parameter='productsmanager_odoo.auto_sync',
        default=True,
    )
    pm_sync_interval = fields.Integer(
        string='Sync Interval (hours)',
        config_parameter='productsmanager_odoo.sync_interval',
        default=6,
    )
