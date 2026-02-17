# Fin manquante du fichier code2asin_config.py à partir de la ligne 640

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
    
    def action_open_monitor(self):
        """Ouvre le monitor d'import avec la nouvelle vue design."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Monitor',
            'res_model': 'code2asin.monitor',
            'view_mode': 'form',
            'target': 'current',
            'context': {'form_view_initial_mode': 'readonly'},
        }
