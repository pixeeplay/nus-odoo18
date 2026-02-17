# -*- coding: utf-8 -*-

from odoo import models, fields, api

class Code2AsinDashboard(models.TransientModel):
    _name = 'code2asin.dashboard'
    _description = 'Code2ASIN Dashboard'

    name = fields.Char(string="Dashboard", default="Code2ASIN Dashboard")

    def action_open_config(self):
        """Ouvre la configuration d'import."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Code2ASIN Configuration',
            'res_model': 'code2asin.config',
            'view_mode': 'form',
            'target': 'new',
            'context': {},
        }

    def action_view_all_logs(self):
        """Affiche tous les logs d'import."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Logs',
            'res_model': 'code2asin.import.log',
            'view_mode': 'list,form',
            'target': 'current',
            'context': {'search_default_group_by_session': 1},
        }

    def action_export_barcode_direct(self):
        """Export direct des codes-barres - Version optimisée pour gros volumes."""
        try:
            # Compter les produits avec codes-barres
            products_count = self.env['product.product'].search_count([
                ('barcode', '!=', False),
                ('barcode', '!=', '')
            ])
            
            if products_count == 0:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Aucun produit trouvé',
                        'message': "Aucun produit avec code-barres trouvé pour l'export.",
                        'type': 'warning',
                    }
                }
            
            # Log du début d'export
            import_session_id = fields.Datetime.now().strftime('%Y%m%d%H%M%S')
            log_model = self.env['code2asin.import.log']
            
            log_model.create({
                'name': f"🔄 Début export codes-barres: {products_count} produits à traiter",
                'log_type': 'info',
                'import_session_id': f'export_{import_session_id}'
            })
            
            # Créer le CSV avec traitement par batch
            import io
            import csv
            from datetime import datetime
            
            output = io.StringIO()
            writer = csv.writer(output, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            
            # En-têtes
            writer.writerow(['EAN', 'Nom du produit', 'Référence interne', 'Prix de vente'])
            
            # Traitement par batch de 1000 produits pour éviter les timeouts
            batch_size = 1000
            offset = 0
            exported_count = 0
            
            while offset < products_count:
                products = self.env['product.product'].search([
                    ('barcode', '!=', False),
                    ('barcode', '!=', '')
                ], limit=batch_size, offset=offset)
                
                for product in products:
                    writer.writerow([
                        product.barcode or '',
                        product.name or '',
                        product.default_code or '',
                        product.list_price or 0.0
                    ])
                    exported_count += 1
                
                offset += batch_size
                
                # Log de progression
                if exported_count % 5000 == 0 or offset >= products_count:
                    log_model.create({
                        'name': f"📊 Export en cours: {exported_count}/{products_count} produits traités",
                        'log_type': 'info',
                        'import_session_id': f'export_{import_session_id}'
                    })
                
                # Commit périodique pour éviter les timeouts
                self.env.cr.commit()
            
            # Préparer le fichier pour téléchargement
            csv_data = output.getvalue()
            output.close()
            
            # Créer un attachment temporaire
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'code2asin_export_barcodes_{timestamp}.csv'
            
            # Encoder correctement le CSV en base64 pour Odoo
            import base64
            csv_bytes = csv_data.encode('utf-8-sig')  # UTF-8 with BOM pour Excel
            csv_base64 = base64.b64encode(csv_bytes).decode('utf-8')
            
            attachment = self.env['ir.attachment'].create({
                'name': filename,
                'type': 'binary',
                'datas': csv_base64,
                'mimetype': 'text/csv; charset=utf-8',
                'res_model': 'code2asin.dashboard',
                'res_id': self.id,
            })
            
            # Log final
            log_model.create({
                'name': f"✅ Export terminé avec succès: {exported_count} produits exportés dans {filename}",
                'log_type': 'success',
                'import_session_id': f'export_{import_session_id}'
            })
            
            # Retourner l'action de téléchargement
            return {
                'type': 'ir.actions.act_url',
                'url': f'/web/content/{attachment.id}?download=true',
                'target': 'new',
            }
            
        except Exception as e:
            log_model.create({
                'name': f"❌ Erreur lors de l'export: {str(e)}",
                'log_type': 'error',
                'import_session_id': f'export_{import_session_id}'
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur export',
                    'message': f"Erreur lors de l'export: {str(e)}",
                    'type': 'danger',
                }
            }

    def action_view_current_import(self):
        """Monitoring de l'import en cours - Redirige vers la vue monitor."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Monitor',
            'res_model': 'code2asin.monitor',
            'view_mode': 'form',
            'target': 'current',
            'context': {'form_view_initial_mode': 'readonly'},
        }

    def action_start_import_monitor(self):
        """Lance l'import avec monitoring en temps réel."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Monitor',
            'res_model': 'code2asin.config',
            'view_mode': 'form',
            'target': 'new',
            'context': {'show_import_button': True},
        }
