# -*- coding: utf-8 -*-
import csv
import base64
import io
from odoo import api, fields, models, _


class PlanetePimPlanningExportWizard(models.TransientModel):
    _name = "planete.pim.planning.export.wizard"
    _description = "Export Planification avec Templates & Mots de passe"

    name = fields.Char(string="Nom du fichier", default="planification_export.csv")
    export_format = fields.Selection(
        [("csv", "CSV"), ("xlsx", "Excel (XLSX)")],
        string="Format",
        default="csv"
    )
    include_passwords = fields.Boolean(
        string="Inclure mots de passe",
        default=False,
        help="Inclure les mots de passe FTP/SFTP dans l'export (⚠️ À utiliser avec prudence)"
    )
    include_mapping_details = fields.Boolean(
        string="Détails des templates de mapping",
        default=True,
        help="Inclure tous les champs mappés pour chaque template"
    )
    csv_data = fields.Binary(
        string="Fichier CSV",
        readonly=True,
        help="Fichier d'export généré"
    )
    csv_filename = fields.Char(
        string="Nom du fichier",
        readonly=True
    )

    def action_export(self):
        """Exporte les données de Planification en CSV."""
        self.ensure_one()
        
        # Générer le CSV
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_MINIMAL)
        
        # En-têtes principaux
        headers = [
            'Nom Fournisseur',
            'Société',
            'Protocole',
            'Host',
            'Port',
            'Username',
            'Statut Connexion',
            'Dernière Exécution',
            'Dernière Erreur',
        ]
        
        # Ajouter les mots de passe si demandé
        if self.include_passwords:
            headers.extend(['Mot de passe', 'Clé SSH'])
        
        # Ajouter les colonnes planification
        headers.extend([
            'Planif. FTP Active',
            'Niveau FTP',
            'Cron FTP Actif',
            'Planif. PIM Active',
            'Niveau PIM',
            'Cron PIM Actif',
            'Template Mapping (Nom)',
        ])
        
        # Ajouter les colonnes détails mapping si demandé
        if self.include_mapping_details:
            headers.extend(['Champs Mappés', 'Transformation'])
        
        writer.writerow(headers)
        
        # Récupérer tous les providers
        providers = self.env["ftp.provider"].search([])
        
        # Écrire les données pour chaque provider
        for provider in providers:
            row = [
                provider.name or '',
                provider.company_id.name if provider.company_id else '',
                provider.protocol or '',
                provider.host or '',
                str(provider.port or ''),
                provider.username or '',
                dict(provider._fields['last_connection_status'].selection).get(
                    provider.last_connection_status, ''
                ) if provider.last_connection_status else '',
                provider.last_run_at.strftime('%Y-%m-%d %H:%M') if provider.last_run_at else '',
                provider.last_error or '',
            ]
            
            # Ajouter les mots de passe si demandé
            if self.include_passwords:
                # ⚠️ Les mots de passe sont cryptés dans Odoo, on les affiche pas directement
                row.append('**CRYPTÉ**')  # Placeholder pour le mot de passe FTP
                row.append('**CRYPTÉ**')  # Placeholder pour la clé SSH
            
            # Ajouter les colonnes planification
            row.extend([
                'Oui' if provider.auto_process else 'Non',
                provider.schedule_level or '',
                'Oui' if provider.schedule_active else 'Non',
                'Oui' if provider.schedule_pim_active else 'Non',
                provider.schedule_pim_level or '',
                'Oui' if getattr(provider, 'pim_cron_active', False) else 'Non',
                provider.mapping_template_id.name if provider.mapping_template_id else '(Aucun)',
            ])
            
            # Ajouter les détails du mapping si demandé et template disponible
            if self.include_mapping_details and provider.mapping_template_id:
                mapping_details = self._get_mapping_details(provider.mapping_template_id)
                row.append(mapping_details['fields'])
                row.append(mapping_details['transforms'])
            elif self.include_mapping_details:
                row.extend(['', ''])
            
            writer.writerow(row)
        
        # Ajouter une section "Templates de Mapping" avec détails complets
        writer.writerow([])
        writer.writerow(['=== TEMPLATES DE MAPPING ==='])
        writer.writerow([])
        
        # Lister tous les templates
        templates = self.env["ftp.mapping.template"].search([])
        
        for template in templates:
            writer.writerow([f'Template: {template.name}', f'ID: {template.id}'])
            
            # En-têtes pour les lignes de mapping
            mapping_headers = [
                'Colonne CSV',
                'Champ Cible',
                'Type Transformation',
                'Valeur Transformation',
                'Valeur Transformation 2',
                'Colonnes Concaténation',
                'Séparateur',
                'Actif'
            ]
            writer.writerow(mapping_headers)
            
            # Écrire les lignes de mapping
            for line in template.line_ids:
                mapping_row = [
                    line.source_column or '',
                    line.target_field or '',
                    line.transform_type or '',
                    line.transform_value or '',
                    line.transform_value2 or '',
                    line.concat_column or '',
                    line.concat_separator or '',
                    'Oui' if line.active else 'Non',
                ]
                writer.writerow(mapping_row)
            
            writer.writerow([])
        
        # Créer l'attachement
        csv_data = output.getvalue().encode('utf-8')
        
        self.write({
            'csv_data': base64.b64encode(csv_data),
            'csv_filename': self.name,
        })
        
        # Retourner l'action pour télécharger le fichier
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self.id}/csv_data/{self.name}?download=true',
            'target': 'self',
        }

    def _get_mapping_details(self, template):
        """Retourne un dictionnaire avec les détails du mapping."""
        fields_str = ""
        transforms_str = ""
        
        for line in template.line_ids.filtered(lambda l: l.active):
            if fields_str:
                fields_str += "; "
            fields_str += f"{line.source_column}→{line.target_field}"
            
            if line.transform_type and line.transform_type != 'none':
                if transforms_str:
                    transforms_str += "; "
                transforms_str += f"{line.target_field}({line.transform_type})"
        
        return {
            'fields': fields_str or '(Aucun)',
            'transforms': transforms_str or '(Aucune)',
        }
