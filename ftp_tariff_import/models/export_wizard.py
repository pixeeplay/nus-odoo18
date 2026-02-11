# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import csv
import io
from datetime import datetime
import base64

class FtpExportWizard(models.TransientModel):
    """Wizard pour exporter les providers en CSV"""
    _name = "ftp.export.wizard"
    _description = "Export Providers to CSV"

    export_type = fields.Selection(
        selection=[("providers", "FTP Providers"), ("templates", "Mapping Templates"), ("brands", "Marques + Alias")],
        default="providers",
        string="Type d'export",
        required=True
    )
    
    csv_file = fields.Binary(
        string="Fichier CSV",
        readonly=True,
        help="Fichier CSV à télécharger"
    )
    csv_filename = fields.Char(
        string="Nom du fichier",
        readonly=True,
    )
    state = fields.Selection(
        selection=[("prepare", "Préparation"), ("download", "Téléchargement")],
        default="prepare",
    )

    def action_export(self):
        """Préparer l'export selon le type"""
        self.ensure_one()
        
        if self.export_type == "providers":
            csv_content = self._export_providers_csv()
            filename = f"ftp_providers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        elif self.export_type == "templates":
            csv_content = self._export_templates_csv()
            filename = f"mapping_templates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        elif self.export_type == "brands":
            csv_content = self._export_brands_csv()
            filename = f"brands_alias_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        # Encoder en base64
        csv_bytes = csv_content.encode('utf-8')
        csv_b64 = base64.b64encode(csv_bytes).decode('utf-8')
        
        self.write({
            'csv_file': csv_b64,
            'csv_filename': filename,
            'state': 'download',
        })
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _export_providers_csv(self):
        """Exporter les FTP providers en CSV"""
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
        
        # Headers
        headers = [
            'name', 'active', 'protocol', 'host', 'port', 'username', 'password',
            'remote_dir_in', 'remote_dir_processed', 'remote_dir_error',
            'file_pattern', 'csv_delimiter', 'csv_encoding', 'csv_has_header',
            'barcode_columns', 'price_column', 'auto_process', 'schedule_active'
        ]
        writer.writerow(headers)
        
        # Data
        providers = self.env['ftp.provider'].search([])
        for provider in providers:
            row = [
                provider.name,
                'Yes' if provider.active else 'No',
                provider.protocol or '',
                provider.host or '',
                provider.port or '',
                provider.username or '',
                provider.password or '',
                provider.remote_dir_in or '',
                provider.remote_dir_processed or '',
                provider.remote_dir_error or '',
                provider.file_pattern or '',
                provider.csv_delimiter or '',
                provider.csv_encoding or '',
                'Yes' if provider.csv_has_header else 'No',
                provider.barcode_columns or '',
                provider.price_column or '',
                'Yes' if provider.auto_process else 'No',
                'Yes' if provider.schedule_active else 'No',
            ]
            writer.writerow(row)
        
        return output.getvalue()

    def _export_templates_csv(self):
        """Exporter les templates de mapping en CSV"""
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
        
        # Headers
        headers = [
            'provider_name', 'template_name', 'column_name', 'field_name',
            'data_type', 'default_value', 'is_required'
        ]
        writer.writerow(headers)
        
        # Data
        templates = self.env['ftp.mapping.template'].search([])
        for template in templates:
            for mapping in template.mapping_line_ids:
                row = [
                    template.provider_id.name if template.provider_id else '',
                    template.name or '',
                    mapping.column_name or '',
                    mapping.field_name or '',
                    mapping.data_type or '',
                    mapping.default_value or '',
                    'Yes' if mapping.is_required else 'No',
                ]
                writer.writerow(row)
        
        return output.getvalue()

    def _export_brands_csv(self):
        """Exporter les marques + alias en CSV"""
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
        
        # Headers
        headers = ['brand_name', 'brand_code', 'alias']
        writer.writerow(headers)
        
        # Data
        brands = self.env['product.brand'].search([])
        for brand in brands:
            # Rechercher les alias associés
            aliases = self.env['product.brand.alias'].search([('brand_id', '=', brand.id)])
            
            if aliases:
                for alias in aliases:
                    row = [
                        brand.name or '',
                        brand.code if hasattr(brand, 'code') and brand.code else '',
                        alias.name or '',
                    ]
                    writer.writerow(row)
            else:
                # Au moins une ligne même sans alias
                row = [
                    brand.name or '',
                    brand.code if hasattr(brand, 'code') and brand.code else '',
                    '',
                ]
                writer.writerow(row)
        
        return output.getvalue()
