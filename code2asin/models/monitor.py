# -*- coding: utf-8 -*-

import re
from datetime import datetime, timedelta
from pytz import timezone
from odoo import models, fields, api
from odoo.exceptions import UserError

class Code2AsinMonitor(models.TransientModel):
    _name = 'code2asin.monitor'
    _description = 'Code2ASIN Import Monitor'

    # Informations de session
    import_session_id = fields.Char(string="Session ID", readonly=True)
    session_title = fields.Char(string="Session", readonly=True)
    current_status = fields.Char(string="Status", readonly=True)
    import_running = fields.Boolean(string="Import Running", readonly=True)
    
    # Informations fichier
    filename = fields.Char(string="Fichier", readonly=True)
    supplier_name = fields.Char(string="Fournisseur CSV", readonly=True)
    file_size = fields.Char(string="Taille", readonly=True)
    
    # Statistiques
    total_products = fields.Integer(string="Total", readonly=True)
    processed_count = fields.Integer(string="Traités", readonly=True)
    success_count = fields.Integer(string="Succès", readonly=True)
    error_count = fields.Integer(string="Erreurs", readonly=True)
    images_processed = fields.Integer(string="Images", readonly=True)
    
    # Progression
    progress_percentage = fields.Float(string="Progression %", readonly=True)
    progress_text = fields.Char(string="Statut", readonly=True)
    
    # Temps
    start_time = fields.Char(string="Démarrage", readonly=True)
    estimated_end_time = fields.Char(string="Fin estimée", readonly=True)
    duration = fields.Char(string="Durée", readonly=True)
    
    # Performance
    products_per_minute = fields.Char(string="Produits/min", readonly=True)
    current_phase = fields.Char(string="Phase actuelle", readonly=True)
    
    # Résumé
    last_actions = fields.Html(string="Dernières Actions", readonly=True)

    @api.model
    def default_get(self, fields_list):
        """Charger les données de monitoring en temps réel."""
        res = super().default_get(fields_list)
        
        # Récupérer la session courante
        current_session = self.env['ir.config_parameter'].sudo().get_param('code2asin.current_import_session', '')
        import_running = self.env['ir.config_parameter'].sudo().get_param('code2asin.import_running', 'False') == 'True'
        
        # Si pas de session courante, chercher la dernière session récente
        if not current_session:
            recent_logs = self.env['code2asin.import.log'].search([
                ('create_date', '>', fields.Datetime.now() - timedelta(hours=1))
            ], order='create_date desc', limit=1)
            
            if recent_logs:
                current_session = recent_logs[0].import_session_id
        
        if current_session:
            # Charger les données de la session
            session_data = self._load_session_data(current_session, import_running)
            res.update(session_data)
        else:
            # Pas de session trouvée
            res.update({
                'session_title': 'Aucune session trouvée',
                'current_status': 'Aucun import récent ou en cours',
                'import_running': False,
                'progress_text': 'Aucun import détecté dans la dernière heure'
            })
        
        return res
    
    def _load_session_data(self, session_id, is_running):
        """Charge les données détaillées d'une session."""
        logs = self.env['code2asin.import.log'].search([
            ('import_session_id', '=', session_id)
        ], order='create_date asc')
        
        if not logs:
            return {
                'session_title': f'Session {session_id}',
                'current_status': 'Session vide',
                'import_running': False
            }
        
        # Analyser les logs pour extraire les statistiques
        stats = self._analyze_logs(logs, is_running)
        
        # Informations de base
        data = {
            'import_session_id': session_id,
            'session_title': f'Session {session_id}',
            'current_status': '🔄 Import en cours...' if is_running else '✅ Import terminé',
            'import_running': is_running,
        }
        
        # Informations fichier depuis les logs ou paramètres - seulement si disponibles
        filename = self.env['ir.config_parameter'].sudo().get_param('code2asin.filename', '')
        supplier = self.env['ir.config_parameter'].sudo().get_param('code2asin.csv_supplier_name', '')
        file_size = self.env['ir.config_parameter'].sudo().get_param('code2asin.file_size', '')
        
        # Ajouter seulement les informations disponibles (non vides)
        if filename:
            data['filename'] = filename
        if supplier:
            data['supplier_name'] = supplier
        if file_size:
            data['file_size'] = file_size
        
        # Statistiques
        data.update(stats)
        
        # Résumé des dernières actions
        data['last_actions'] = self._generate_actions_summary(logs, is_running)
        
        return data
    
    def _analyze_logs(self, logs, is_running):
        """Analyse les logs pour extraire les statistiques."""
        paris_tz = timezone('Europe/Paris')
        
        # Rechercher les informations dans les logs
        total_products = 0
        processed_count = 0
        created_count = 0
        updated_count = 0
        error_count = 0
        images_count = 0
        start_time = None
        current_phase = "Initialisation"
        
        # Pattern pour extraire les nombres des logs
        total_pattern = r'(\d+) lignes de produits à traiter'
        progress_pattern = r'Progression: (\d+)/(\d+)'
        created_pattern = r'Nouveaux produits créés: (\d+)'
        updated_pattern = r'Produits mis à jour: (\d+)'
        error_pattern = r'Erreurs rencontrées: (\d+)'
        images_pattern = r'(\d+) images importées avec succès'
        
        for log in logs:
            log_name = log.name
            
            # Démarrage
            if 'DÉMARRAGE IMPORT' in log_name:
                if log.create_date:
                    # Convertir la date UTC vers timezone Paris
                    start_time = log.create_date.replace(tzinfo=timezone('UTC')).astimezone(paris_tz)
                current_phase = "Démarrage"
            
            # Total produits à traiter
            match = re.search(total_pattern, log_name)
            if match:
                total_products = int(match.group(1))
                current_phase = "Analyse du fichier"
            
            # Progression
            match = re.search(progress_pattern, log_name)
            if match:
                processed_count = int(match.group(1))
                current_phase = f"Traitement produits ({processed_count}/{total_products})"
            
            # Statistiques finales
            if 'IMPORT TERMINÉ' in log_name:
                current_phase = "Terminé"
                # Extraire les stats finales
                match = re.search(created_pattern, log_name)
                if match:
                    created_count = int(match.group(1))
                
                match = re.search(updated_pattern, log_name)
                if match:
                    updated_count = int(match.group(1))
                
                match = re.search(error_pattern, log_name)
                if match:
                    error_count = int(match.group(1))
            
            # Images
            if 'images importées avec succès' in log_name:
                match = re.search(images_pattern, log_name)
                if match:
                    images_count += int(match.group(1))
        
        # Calculs
        success_count = created_count + updated_count
        progress_percentage = (processed_count / total_products * 100) if total_products > 0 else 0
        
        # Temps et performance
        start_time_str = start_time.strftime('%d/%m/%Y %H:%M:%S (Paris)') if start_time else 'Non disponible'
        
        products_per_minute = "0"
        estimated_end = "Calcul en cours..."
        duration_str = "En cours..."
        
        if start_time and processed_count > 0:
            elapsed = datetime.now(paris_tz) - start_time
            elapsed_minutes = elapsed.total_seconds() / 60
            
            if elapsed_minutes > 0:
                products_per_minute = f"{(processed_count / elapsed_minutes):.1f}"
                
                if is_running and total_products > processed_count:
                    remaining = total_products - processed_count
                    remaining_minutes = remaining / (processed_count / elapsed_minutes)
                    estimated_end_time = datetime.now(paris_tz) + timedelta(minutes=remaining_minutes)
                    estimated_end = estimated_end_time.strftime('%H:%M:%S')
            
            if not is_running:
                duration_str = f"{int(elapsed_minutes)}min {int(elapsed.total_seconds() % 60)}s"
        
        return {
            'total_products': total_products,
            'processed_count': processed_count,
            'success_count': success_count,
            'error_count': error_count,
            'images_processed': images_count,
            'progress_percentage': progress_percentage,
            'progress_text': f"{processed_count}/{total_products} produits traités ({progress_percentage:.1f}%)",
            'start_time': start_time_str,
            'estimated_end_time': estimated_end,
            'duration': duration_str,
            'products_per_minute': products_per_minute,
            'current_phase': current_phase,
        }
    
    def _generate_actions_summary(self, logs, is_running):
        """Génère un résumé HTML des dernières actions."""
        if not logs:
            return "<p>Aucune action enregistrée.</p>"
        
        # Prendre les 10 derniers logs significatifs
        significant_logs = []
        for log in logs.sorted('create_date', reverse=True):
            if any(keyword in log.name.lower() for keyword in ['progression', 'terminé', 'démarrage', 'erreur', 'succès']):
                significant_logs.append(log)
                if len(significant_logs) >= 10:
                    break
        
        html = "<div style='font-family: monospace; font-size: 12px;'>"
        
        for log in reversed(significant_logs):  # Ordre chronologique
            # Couleur selon le type
            color = "#28a745"  # vert
            icon = "✅"
            
            if log.log_type == 'error':
                color = "#dc3545"  # rouge
                icon = "❌"
            elif log.log_type == 'warning':
                color = "#ffc107"  # orange
                icon = "⚠️"
            elif log.log_type == 'info':
                color = "#17a2b8"  # bleu
                icon = "ℹ️"
            
            time_str = log.create_date.strftime('%H:%M:%S') if log.create_date else ''
            
            html += f"""
            <div style='margin: 2px 0; padding: 2px; border-left: 3px solid {color};'>
                <span style='color: {color}; font-weight: bold;'>{icon} {time_str}</span>
                <span style='margin-left: 10px;'>{log.name[:100]}{'...' if len(log.name) > 100 else ''}</span>
            </div>
            """
        
        html += "</div>"
        return html
    
    def action_refresh_status(self):
        """Rafraîchit le statut du monitoring."""
        # Recharger les données
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Monitor',
            'res_model': 'code2asin.monitor',
            'view_mode': 'form',
            'target': 'current',
            'context': {'form_view_initial_mode': 'readonly'},
        }
    
    def action_stop_import(self):
        """Arrête l'import en cours."""
        self.env['ir.config_parameter'].sudo().set_param('code2asin.import_running', 'False')
        
        # Log de l'arrêt
        if self.import_session_id:
            self.env['code2asin.import.log'].create({
                'name': f"⏹️ Import arrêté manuellement par {self.env.user.name}",
                'log_type': 'warning',
                'import_session_id': self.import_session_id
            })
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Import arrêté',
                'message': "L'importation a été arrêtée. Le processus va s'interrompre proprement.",
                'sticky': True,
                'type': 'warning',
            }
        }
    
    def action_view_detailed_logs(self):
        """Affiche les logs détaillés de la session."""
        if not self.import_session_id:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Aucune session',
                    'message': "Aucune session d'import à afficher.",
                    'type': 'warning',
                }
            }
        
        return {
            'type': 'ir.actions.act_window',
            'name': f'Logs Détaillés - {self.import_session_id}',
            'res_model': 'code2asin.import.log',
            'view_mode': 'list,form',
            'domain': [('import_session_id', '=', self.import_session_id)],
            'target': 'current',
            'context': {
                'search_default_import_session_id': self.import_session_id,
            }
        }
