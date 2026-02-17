import base64
import csv
import logging
import requests
from datetime import datetime, timedelta
from pytz import timezone
from odoo import models, fields, api
from odoo.exceptions import UserError

_LOGGER = logging.getLogger(__name__)

class Code2AsinConfig(models.TransientModel):
    _name = 'code2asin.config'
    _description = 'Code2ASIN Import Configuration'

    # Fichier CSV
    csv_file = fields.Binary(string="CSV File")
    filename = fields.Char(string="Filename")
    csv_supplier_name = fields.Char(string="Supplier Name", help="Nom du fournisseur du fichier CSV", required=True, default="Fournisseur CSV")
    
    # Option pour ignorer les EAN existants
    skip_existing_ean = fields.Boolean(string="Skip existing EAN", default=False, 
                                      help="Ignorer les produits avec des EAN déjà présents dans la base de données")
    
    # Champs stockés pour les informations du fichier
    file_size_display = fields.Char(string="File Size", default="-")
    file_name_display = fields.Char(string="File Name", default="-")
    
    @api.onchange('csv_file', 'filename')
    def _onchange_csv_file(self):
        """Met à jour les informations d'affichage du fichier."""
        if self.csv_file and self.filename:
            try:
                # Calculer la taille du fichier en base64
                csv_data = base64.b64decode(self.csv_file)
                size_bytes = len(csv_data)
                
                if size_bytes < 1024:
                    self.file_size_display = f"{size_bytes} bytes"
                elif size_bytes < 1024 * 1024:
                    self.file_size_display = f"{(size_bytes / 1024):.1f} KB"
                else:
                    self.file_size_display = f"{(size_bytes / (1024 * 1024)):.1f} MB"
                
                self.file_name_display = self.filename
                
                # Calculer le nombre de lignes et colonnes pour information
                try:
                    validation_helper = self.env['code2asin.validation.helper']
                    csv_file_lines = validation_helper.parse_csv_with_encoding(csv_data)
                    reader = csv.reader(csv_file_lines, delimiter=',', quotechar='"')
                    headers = next(reader, [])
                    data_rows = list(reader)
                    
                    self.file_size_display += f" ({len(data_rows)} lignes, {len(headers)} colonnes)"
                except:
                    pass
                    
            except Exception as e:
                _LOGGER.debug(f"Erreur calcul info fichier: {e}")
                self.file_size_display = "Erreur calcul"
                self.file_name_display = self.filename or "Fichier invalide"
        else:
            self._update_file_info_from_params()
    
    def _update_file_info_from_params(self):
        """Met à jour les informations du fichier depuis les paramètres sauvegardés."""
        try:
            saved_filename = self.env['ir.config_parameter'].sudo().get_param('code2asin.filename', '')
            saved_file_size = self.env['ir.config_parameter'].sudo().get_param('code2asin.file_size', '')
            saved_supplier = self.env['ir.config_parameter'].sudo().get_param('code2asin.csv_supplier_name', '')
            
            if saved_filename and saved_file_size:
                self.file_name_display = saved_filename
                self.file_size_display = saved_file_size
                self.csv_supplier_name = saved_supplier
            else:
                self.file_size_display = "-"
                self.file_name_display = "-"
        except:
            self.file_size_display = "-"
            self.file_name_display = "-"
    
    # Options d'importation avec boutons on/off
    import_name = fields.Boolean(string="Import Product Name", default=True)
    name_update_mode = fields.Selection([
        ('update', 'Update if empty'),
        ('replace', 'Always replace')
    ], string="Name Update Mode", default='update')
    
    import_default_code = fields.Boolean(string="Import Internal Reference", default=True)
    default_code_update_mode = fields.Selection([
        ('update', 'Update if empty'),
        ('replace', 'Always replace')
    ], string="Reference Update Mode", default='update')
    
    import_price = fields.Boolean(string="Import Sale Price", default=True)
    price_update_mode = fields.Selection([
        ('update', 'Update if zero'),
        ('replace', 'Always replace')
    ], string="Price Update Mode", default='update')
    
    import_weight = fields.Boolean(string="Import Weight", default=True)
    weight_update_mode = fields.Selection([
        ('update', 'Update if zero'),
        ('replace', 'Always replace')
    ], string="Weight Update Mode", default='update')
    
    import_dimensions = fields.Boolean(string="Import Dimensions", default=True)
    dimensions_update_mode = fields.Selection([
        ('update', 'Update if empty'),
        ('replace', 'Always replace')
    ], string="Dimensions Update Mode", default='update')
    
    import_brand = fields.Boolean(string="Import Brand", default=True)
    brand_update_mode = fields.Selection([
        ('update', 'Update if empty'),
        ('replace', 'Always replace')
    ], string="Brand Update Mode", default='update')
    
    import_color = fields.Boolean(string="Import Color", default=True)
    color_update_mode = fields.Selection([
        ('update', 'Update if empty'),
        ('replace', 'Always replace')
    ], string="Color Update Mode", default='update')
    
    import_description = fields.Boolean(string="Import Description", default=True)
    description_update_mode = fields.Selection([
        ('update', 'Update if empty'),
        ('replace', 'Always replace')
    ], string="Description Update Mode", default='update')
    
    import_images = fields.Boolean(string="Import Images", default=True)
    images_update_mode = fields.Selection([
        ('update', 'Update if empty'),
        ('replace', 'Always replace')
    ], string="Images Update Mode", default='update')
    
    # Champ technique pour l'interface
    import_running = fields.Boolean(string="Import Running", readonly=True, compute='_compute_import_running')
    
    @api.depends('csv_file')
    def _compute_import_running(self):
        """Vérifie si un import est en cours d'exécution."""
        for record in self:
            # Récupérer le statut d'import mais avec mécanisme de reset automatique
            import_running = self.env['ir.config_parameter'].sudo().get_param('code2asin.import_running', 'False') == 'True'
            
            # CORRECTION: Reset automatique si import_running est bloqué
            if import_running:
                # Vérifier s'il y a vraiment un import en cours récent (moins de 10 minutes)
                current_session = self.env['ir.config_parameter'].sudo().get_param('code2asin.current_import_session', '')
                if current_session:
                    # Chercher des logs récents de cette session
                    ten_minutes_ago = fields.Datetime.now() - timedelta(minutes=10)
                    recent_logs = self.env['code2asin.import.log'].search([
                        ('import_session_id', '=', current_session),
                        ('create_date', '>=', ten_minutes_ago)
                    ], limit=1)
                    
                    if not recent_logs:
                        # Aucun log récent, probablement un import bloqué - reset automatique
                        _LOGGER.warning("Import status reset automatique - aucun log récent trouvé")
                        self.env['ir.config_parameter'].sudo().set_param('code2asin.import_running', 'False')
                        import_running = False
            
            record.import_running = import_running
    
    def action_stop_import(self):
        """Arrête l'import en cours."""
        self.env['ir.config_parameter'].sudo().set_param('code2asin.import_running', 'False')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Import arrêté',
                'message': "L'importation a été arrêtée. Veuillez attendre que le processus se termine proprement.",
                'sticky': True,
                'type': 'warning',
            }
        }

    def action_get_current_import_status(self):
        """Retourne le statut de l'import en cours pour le monitoring."""
        is_running = self.env['ir.config_parameter'].sudo().get_param('code2asin.import_running', 'False') == 'True'
        current_session = self.env['ir.config_parameter'].sudo().get_param('code2asin.current_import_session', '')
        
        if is_running and current_session:
            # Compter les logs de cette session
            logs_count = self.env['code2asin.import.log'].search_count([
                ('import_session_id', '=', current_session)
            ])
            
            # Trouver le dernier log de progression
            last_progress = self.env['code2asin.import.log'].search([
                ('import_session_id', '=', current_session),
                ('name', 'ilike', 'Progression:')
            ], order='create_date desc', limit=1)
            
            progress_info = last_progress.name if last_progress else "Import en cours..."
            
            return {
                'status': 'running',
                'session_id': current_session,
                'logs_count': logs_count,
                'progress': progress_info
            }
        else:
            return {
                'status': 'idle',
                'session_id': '',
                'logs_count': 0,
                'progress': 'Aucun import en cours'
            }
    
    @api.model
    def default_get(self, fields_list):
        """Charger les valeurs par défaut depuis les paramètres système."""
        res = super().default_get(fields_list)
        
        # Récupérer les valeurs des paramètres système
        params = [
            'csv_file', 'filename', 'csv_supplier_name', 'file_size_display', 'file_name_display',
            'skip_existing_ean',
            'import_name', 'name_update_mode',
            'import_default_code', 'default_code_update_mode',
            'import_price', 'price_update_mode',
            'import_weight', 'weight_update_mode',
            'import_dimensions', 'dimensions_update_mode',
            'import_brand', 'brand_update_mode',
            'import_color', 'color_update_mode',
            'import_description', 'description_update_mode',
            'import_images', 'images_update_mode'
        ]
        
        for param in params:
            if param in fields_list:
                value = self.env['ir.config_parameter'].sudo().get_param(f'code2asin.{param}', default='')
                if param == 'csv_file':
                    # Ne pas charger automatiquement le fichier CSV pour éviter les problèmes d'encodage
                    value = False
                elif param in ['file_size_display', 'file_name_display']:
                    # Récupérer les informations fichier depuis les paramètres sauvegardés
                    if param == 'file_size_display':
                        value = self.env['ir.config_parameter'].sudo().get_param('code2asin.file_size', '-')
                    else:  # file_name_display
                        value = self.env['ir.config_parameter'].sudo().get_param('code2asin.filename', '-')
                elif param == 'csv_supplier_name':
                    value = self.env['ir.config_parameter'].sudo().get_param('code2asin.csv_supplier_name', '')
                elif param in ['skip_existing_ean'] or (param.startswith('import_') and not param.endswith('_mode')):
                    # Pour les champs booléens d'importation et skip_existing_ean
                    value = value == 'True' if value else (True if param.startswith('import_') else False)
                elif param.endswith('_mode'):
                    # Pour les champs de sélection, utiliser la valeur par défaut si vide ou invalide
                    valid_values = ['update', 'replace']
                    if not value or value not in valid_values:
                        if 'price' in param or 'weight' in param:
                            value = 'update'  # Update if zero
                        else:
                            value = 'update'  # Update if empty
                res[param] = value
        
        # Gérer skip_existing_ean spécialement
        if 'skip_existing_ean' in fields_list:
            skip_value = self.env['ir.config_parameter'].sudo().get_param('code2asin.skip_existing_ean', 'False')
            res['skip_existing_ean'] = skip_value == 'True'
        
        # Forcer la mise à jour des informations fichier depuis les paramètres sauvés
        try:
            saved_filename = self.env['ir.config_parameter'].sudo().get_param('code2asin.filename', '')
            saved_file_size = self.env['ir.config_parameter'].sudo().get_param('code2asin.file_size', '')
            saved_supplier = self.env['ir.config_parameter'].sudo().get_param('code2asin.csv_supplier_name', '')
            
            if saved_filename and saved_file_size:
                if 'file_name_display' in fields_list:
                    res['file_name_display'] = saved_filename
                if 'file_size_display' in fields_list:
                    res['file_size_display'] = saved_file_size
                if 'csv_supplier_name' in fields_list:
                    res['csv_supplier_name'] = saved_supplier
        except:
            pass
                
        return res

    def save_config(self):
        """Sauvegarder la configuration."""
        # Enregistrer les options ET les informations du fichier
        params = [
            'csv_supplier_name', 'skip_existing_ean',
            'import_name', 'name_update_mode',
            'import_default_code', 'default_code_update_mode',
            'import_price', 'price_update_mode',
            'import_weight', 'weight_update_mode',
            'import_dimensions', 'dimensions_update_mode',
            'import_brand', 'brand_update_mode',
            'import_color', 'color_update_mode',
            'import_description', 'description_update_mode',
            'import_images', 'images_update_mode'
        ]
        
        for param in params:
            value = getattr(self, param, '')
            self.env['ir.config_parameter'].sudo().set_param(f'code2asin.{param}', str(value))
        
        # Sauvegarder aussi les informations du fichier pour l'affichage ET le fichier pour l'import
        if self.csv_file and self.filename:
            # Sauvegarder le fichier CSV pour l'import
            self.env['ir.config_parameter'].sudo().set_param('code2asin.last_csv_file', self.csv_file)
            self.env['ir.config_parameter'].sudo().set_param('code2asin.filename', self.filename)
            
            # Calculer et sauvegarder la taille avec détails
            try:
                csv_data = base64.b64decode(self.csv_file)
                size_bytes = len(csv_data)
                
                # Calculer taille formatée
                if size_bytes < 1024:
                    file_size = f"{size_bytes} bytes"
                elif size_bytes < 1024 * 1024:
                    file_size = f"{(size_bytes / 1024):.1f} KB"
                else:
                    file_size = f"{(size_bytes / (1024 * 1024)):.1f} MB"
                
                # Ajouter informations lignes/colonnes
                try:
                    validation_helper = self.env['code2asin.validation.helper']
                    csv_file_lines = validation_helper.parse_csv_with_encoding(csv_data)
                    reader = csv.reader(csv_file_lines, delimiter=',', quotechar='"')
                    headers = next(reader, [])
                    data_rows = list(reader)
                    
                    file_size += f" ({len(data_rows)} lignes, {len(headers)} colonnes)"
                except:
                    pass
                
                self.env['ir.config_parameter'].sudo().set_param('code2asin.file_size', file_size)
                # Forcer l'affichage immédiat
                self.file_size_display = file_size
                self.file_name_display = self.filename
            except:
                self.env['ir.config_parameter'].sudo().set_param('code2asin.file_size', 'Erreur calcul')
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Configuration sauvegardée',
                'message': "Les paramètres d'importation ont été sauvegardés avec succès.",
                'sticky': False,
                'type': 'success',
            }
        }

    # Méthodes déléguées aux helpers
    def _parse_csv_with_encoding(self, csv_data):
        """Parse le CSV en utilisant le helper de validation."""
        validation_helper = self.env['code2asin.validation.helper']
        return validation_helper.parse_csv_with_encoding(csv_data)

    def _map_csv_columns(self, headers):
        """Mappe les colonnes CSV en utilisant le helper de validation."""
        validation_helper = self.env['code2asin.validation.helper']
        return validation_helper.map_csv_columns(headers)

    def _validate_and_clean_barcode(self, barcode):
        """Valide et nettoie un code-barres en utilisant le helper de validation."""
        validation_helper = self.env['code2asin.validation.helper']
        return validation_helper.validate_and_clean_barcode(barcode)

    def action_import_products_from_code2asin(self):
        """Lance l'import des produits depuis un fichier CSV Code2ASIN avec redirection vers les logs."""
        if not self.csv_file:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fichier requis',
                    'message': "Veuillez d'abord charger un fichier CSV.",
                    'sticky': False,
                    'type': 'warning',
                }
            }
        
        # Import direct avec ID de session
        import_session_id = f"import_{self.env.user.id}_{fields.Datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # LOGS IMMÉDIAT AVANT TOUTE ANALYSE
        log_model = self.env['code2asin.import.log']
        
        # Préparation des informations contextuelles (fournisseur maintenant obligatoire)
        paris_tz = timezone('Europe/Paris')
        start_time = datetime.now(paris_tz)
        supplier_info = f" - Fournisseur: {self.csv_supplier_name or 'Fournisseur CSV'}"
        filename_info = f" - Fichier: {self.filename}" if self.filename else ""
        skip_info = f" - Skip EAN existants: {'Oui' if self.skip_existing_ean else 'Non'}"
        
        # Log 1 : Démarrage immédiat avec informations contextuelles
        log_model.create({
            'name': f"=== DÉMARRAGE IMPORT CSV CODE2ASIN ==={supplier_info}{filename_info}{skip_info} - {start_time.strftime('%d/%m/%Y %H:%M:%S')} (Paris)",
            'log_type': 'info',
            'import_session_id': import_session_id
        })
        self.env.cr.commit()
        
        # Marquer l'import comme démarré IMMÉDIATEMENT
        self.env['ir.config_parameter'].sudo().set_param('code2asin.import_running', 'True')
        self.env['ir.config_parameter'].sudo().set_param('code2asin.current_import_session', import_session_id)
        self.env.cr.commit()
        
        # Log 2 : Configuration détaillée
        log_model.create({
            'name': f"Session import: {import_session_id} | Utilisateur: {self.env.user.name} | Démarrage: {start_time.strftime('%H:%M:%S')}",
            'log_type': 'info',
            'import_session_id': import_session_id
        })
        self.env.cr.commit()
        
        try:
            # Traitement asynchrone avec le helper dédié
            async_helper = self.env['code2asin.import.async.helper']
            async_helper.process_import_async(self, import_session_id, log_model)
            
            # Rediriger vers la page de monitoring pour le suivi en temps réel
            return {
                'type': 'ir.actions.act_window',
                'name': f'Monitor Import - Session {import_session_id}',
                'res_model': 'code2asin.import.log',
                'view_mode': 'list,form',
                'domain': [('import_session_id', '=', import_session_id)],
                'target': 'current',
                'context': {
                    'current_import_session': import_session_id,
                    'search_default_import_session_id': import_session_id,
                }
            }
                
        except Exception as e:
            _LOGGER.error(f"Erreur import: {e}")
            # Marquer l'import comme terminé
            try:
                self.env['ir.config_parameter'].sudo().set_param('code2asin.import_running', 'False')
                log_model.create({
                    'name': f"ERREUR CRITIQUE: {str(e)}",
                    'log_type': 'error',
                    'import_session_id': import_session_id
                })
                self.env.cr.commit()
            except:
                pass
                
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur import',
                    'message': f"Erreur: {str(e)}",
                    'sticky': True,
                    'type': 'danger',
                }
            }

    def action_open_monitor(self):
        """Ouvre le monitor d'import dans un nouvel onglet."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Monitor',
            'res_model': 'code2asin.monitor',
            'view_mode': 'form',
            'target': 'new',
            'context': {'form_view_initial_mode': 'readonly'},
        }
