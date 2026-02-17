import logging
import base64
import csv
from datetime import datetime
from pytz import timezone
from odoo import models, fields, api

_LOGGER = logging.getLogger(__name__)

class ImportAsyncHelper(models.TransientModel):
    _name = 'code2asin.import.async.helper'
    _description = 'Code2ASIN Import Async Helper'

    def process_import_async(self, config_record, import_session_id, log_model):
        """Traite l'import de manière 'asynchrone' avec logs détaillés."""
        encoding_errors_count = 0
        
        try:
            # Log 3 : Début traitement
            log_model.create({
                'name': "Monitoring activé - traitement démarré",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            # Sauvegarder configuration
            config_record.save_config()
            
            # Log 4 : Chargement fichier
            log_model.create({
                'name': "Chargement et analyse du fichier CSV...",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            # Décoder et analyser le fichier
            csv_data = base64.b64decode(config_record.csv_file)
            file_size_mb = len(csv_data) / (1024 * 1024)
            
            log_model.create({
                'name': f"Fichier chargé: {file_size_mb:.1f} MB",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            csv_file_lines = config_record._parse_csv_with_encoding(csv_data)
            
            log_model.create({
                'name': f"Décodage réussi: {len(csv_file_lines)} lignes totales",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            # Parser le CSV avec protection
            reader = csv.reader(csv_file_lines, delimiter=',', quotechar='"')
            try:
                headers = next(reader, [])
                data_rows = list(reader)
            except Exception as e:
                log_model.create({
                    'name': f"ERREUR parsing CSV: {str(e)}",
                    'log_type': 'error',
                    'import_session_id': import_session_id
                })
                self.env.cr.commit()
                raise
            
            # Log en-têtes et données
            log_model.create({
                'name': f"En-têtes CSV: {', '.join(headers[:5])}{'...' if len(headers) > 5 else ''} (total: {len(headers)} colonnes)",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            log_model.create({
                'name': f"Données CSV: {len(data_rows)} lignes de produits à traiter",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            # Créer mapping
            mapping = config_record._map_csv_columns(headers)
            
            log_model.create({
                'name': f"Mapping créé: {str(mapping)}",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            # Traitement des lignes avec compteurs
            total_lines = len(data_rows)
            processed_count = 0
            created_count = 0
            updated_count = 0
            error_count = 0
            skipped_count = 0
            
            log_model.create({
                'name': f"DÉBUT DU TRAITEMENT DE {total_lines} PRODUITS",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            # Traitement ligne par ligne
            for row_index, row in enumerate(data_rows, start=2):  # Start=2 car ligne 1 = headers
                # Vérifier si l'import doit s'arrêter
                is_running = self.env['ir.config_parameter'].sudo().get_param('code2asin.import_running', 'False')
                if is_running != 'True':
                    log_model.create({
                        'name': f"IMPORT ARRÊTÉ PAR L'UTILISATEUR à la ligne {row_index}",
                        'log_type': 'warning',
                        'import_session_id': import_session_id
                    })
                    break
                
                # Traiter cette ligne
                processor = self.env['code2asin.product.processor']
                result = processor.process_product_row(row, mapping, headers, row_index, import_session_id, log_model, config_record)
                
                # Compter les résultats
                if result == 'created':
                    created_count += 1
                elif result == 'updated':
                    updated_count += 1
                elif result == 'error':
                    error_count += 1
                elif result == 'skipped':
                    skipped_count += 1
                
                processed_count += 1
                
                # Afficher progression tous les 10 produits
                if processed_count % 10 == 0:
                    progress_text = f"Progression: {processed_count}/{total_lines} ({(processed_count/total_lines)*100:.1f}%) - Créés: {created_count}, Mis à jour: {updated_count}, Erreurs: {error_count}, Ignorés: {skipped_count}"
                    log_model.create({
                        'name': progress_text,
                        'log_type': 'info',
                        'import_session_id': import_session_id
                    })
                    self.env.cr.commit()
            
            # Récap final complet avec toutes les statistiques
            success_rate = ((created_count + updated_count) / processed_count * 100) if processed_count > 0 else 0
            
            final_message = f"""=== IMPORT TERMINÉ AVEC SUCCÈS ===

📊 STATISTIQUES FINALES:
• Lignes traitées: {processed_count}/{total_lines}
• Nouveaux produits créés: {created_count}
• Produits mis à jour: {updated_count}
• Erreurs rencontrées: {error_count}
• Lignes ignorées: {skipped_count}
• Erreurs d'encodage détectées: {encoding_errors_count}
• Taux de succès: {success_rate:.1f}%

✅ L'import s'est terminé normalement.
Session: {import_session_id}"""

            log_model.create({
                'name': final_message,
                'log_type': 'success' if error_count == 0 else 'warning',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            
            # Log séparé pour les erreurs d'encodage si nécessaire
            if encoding_errors_count > 0:
                log_model.create({
                    'name': f"⚠️ ATTENTION: {encoding_errors_count} produits avec des erreurs d'encodage détectées dans les titres. Vérifiez la qualité de votre fichier CSV source.",
                    'log_type': 'warning',
                    'import_session_id': import_session_id
                })
                self.env.cr.commit()
            
        except Exception as e:
            log_model.create({
                'name': f"❌ ERREUR IMPORT: {str(e)}",
                'log_type': 'error',
                'import_session_id': import_session_id
            })
            self.env.cr.commit()
            raise
            
        finally:
            # Marquer l'import comme terminé TOUJOURS
            self.env['ir.config_parameter'].sudo().set_param('code2asin.import_running', 'False')
            self.env.cr.commit()
