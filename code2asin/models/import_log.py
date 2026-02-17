# -*- coding: utf-8 -*-
from odoo import models, fields, api

class Code2ASINImportLog(models.Model):
    _name = 'code2asin.import.log'
    _description = 'Code2ASIN Import Log'
    _order = 'create_date desc'

    name = fields.Char(string='Description', required=True)
    user_id = fields.Many2one('res.users', string='User', default=lambda self: self.env.user)
    create_date = fields.Datetime(string='Date', readonly=True)
    log_type = fields.Selection([
        ('info', 'Information'),
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('error', 'Error')
    ], string='Type', default='info')
    import_session_id = fields.Char(string='Import Session', required=True, 
                                   help='Unique identifier for the import session')
    
    @api.model
    def add_log(self, message, log_type='info', import_session_id=None):
        """Add a log entry for the import process."""
        if not import_session_id:
            import_session_id = fields.Datetime.now().strftime('%Y%m%d%H%M%S')
        
        self.create({
            'name': message,
            'log_type': log_type,
            'import_session_id': import_session_id
        })
        
        return import_session_id
    
    def action_stop_import(self):
        """Arrête l'import en cours."""
        self.env['ir.config_parameter'].sudo().set_param('code2asin.import_running', 'False')
        
        # Ajouter un log pour indiquer que l'importation a été arrêtée
        import_session_id = self.import_session_id
        self.env['code2asin.import.log'].create({
            'name': "Importation arrêtée par l'utilisateur",
            'log_type': 'warning',
            'import_session_id': import_session_id
        })
        
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
    
    def action_refresh_logs(self):
        """Rafraîchit la vue des logs."""
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
    
    # Méthodes pour le dashboard
    def action_open_config(self):
        """Ouvre la configuration d'import."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Code2ASIN Configuration',
            'res_model': 'code2asin.config',
            'view_mode': 'form',
            'target': 'new',
            'context': {}
        }
    
    def action_view_all_logs(self):
        """Affiche tous les logs d'import."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Logs',
            'res_model': 'code2asin.import.log',
            'view_mode': 'list,form',
            'target': 'current',
            'context': {}
        }
    
    def action_export_barcode_direct(self):
        """Export direct des codes-barres."""
        config_model = self.env['code2asin.config']
        config = config_model.create({})
        return config.action_export_barcode_for_code2asin()
    
    def action_view_current_import(self):
        """Affiche les logs de l'import en cours."""
        # Récupérer la dernière session d'import
        last_session = self.env['ir.config_parameter'].sudo().get_param('code2asin.last_import_session', '')
        
        if last_session:
            return {
                'type': 'ir.actions.act_window',
                'name': f'Current Import - Session {last_session}',
                'res_model': 'code2asin.import.log',
                'view_mode': 'list,form',
                'domain': [('import_session_id', '=', last_session)],
                'target': 'current',
                'context': {
                    'default_import_session_id': last_session,
                    'search_default_import_session_id': last_session,
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Aucun import en cours',
                    'message': "Aucun import en cours ou récent trouvé.",
                    'type': 'info',
                }
            }
