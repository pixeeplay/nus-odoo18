from odoo import api, fields, models


class PmSyncLog(models.Model):
    _name = 'pm.sync.log'
    _description = 'Products Manager Sync Log'
    _order = 'create_date desc'

    config_id = fields.Many2one('pm.config', string='Configuration', ondelete='set null')
    operation = fields.Selection([
        ('search', 'Search'),
        ('import', 'Import'),
        ('sync', 'Sync'),
        ('error', 'Error'),
    ], required=True)
    message = fields.Text(required=True)
    product_count = fields.Integer(default=0)
    session_id = fields.Char()

    @api.model
    def log(self, config_id=None, operation='info', message='', product_count=0, session_id=False):
        """Create a log entry."""
        self.create({
            'config_id': config_id,
            'operation': operation,
            'message': message,
            'product_count': product_count,
            'session_id': session_id or fields.Datetime.now().strftime('%Y%m%d%H%M%S'),
        })
