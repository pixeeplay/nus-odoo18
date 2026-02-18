# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.addons.magarantie_connector.services.magarantie_api import MaGarantieAPI


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    magarantie_token_test = fields.Char(
        string="MaGarantie Test Token",
        config_parameter='magarantie_connector.token_test',
    )
    magarantie_token_prod = fields.Char(
        string="MaGarantie Production Token",
        config_parameter='magarantie_connector.token_prod',
    )
    magarantie_mode = fields.Selection(
        [('test', 'Test'), ('production', 'Production')],
        string="MaGarantie Mode",
        default='test',
        config_parameter='magarantie_connector.mode',
    )
    magarantie_ip_access = fields.Char(
        string="MaGarantie Authorized IP",
        config_parameter='magarantie_connector.ip_access',
        help="IP address authorized by MaGarantie for API access. "
             "Separate multiple IPs with commas.",
    )

    def action_magarantie_test_connection(self):
        """Test the MaGarantie API connection."""
        self.ensure_one()
        api_client = self._get_magarantie_api()
        try:
            result = api_client.get_categories()
            count = len(result) if isinstance(result, list) else 1
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Connection successful! Found %d categories.') % count,
                    'type': 'success',
                    'sticky': False,
                },
            }
        except Exception as e:
            raise UserError(_("Connection test failed: %s") % str(e))

    def action_magarantie_sync_all(self):
        """Sync categories and warranties from MaGarantie API."""
        cat_count = self.env['magarantie.category'].action_sync_from_api()
        war_count = self.env['magarantie.warranty'].action_sync_from_api()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sync Complete'),
                'message': _('Synced %d categories and %d warranties.') % (
                    cat_count or 0, war_count or 0),
                'type': 'success',
                'sticky': False,
            },
        }

    @api.model
    def _get_magarantie_api(self):
        """Factory method to create a MaGarantieAPI instance with current settings."""
        ICP = self.env['ir.config_parameter'].sudo()
        mode = ICP.get_param('magarantie_connector.mode', 'test')
        if mode == 'production':
            token = ICP.get_param('magarantie_connector.token_prod', '')
        else:
            token = ICP.get_param('magarantie_connector.token_test', '')
        if not token:
            raise UserError(
                _("MaGarantie API token is not configured. "
                  "Go to Settings > MaGarantie.")
            )
        return MaGarantieAPI(token=token)
