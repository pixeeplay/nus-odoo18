# -*- coding: utf-8 -*-
import base64
import json
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class FtpMappingTemplate(models.Model):
    """Template de mapping CSV pour un fournisseur.
    
    Permet de sauvegarder le mapping entre les colonnes d'un fichier CSV
    et les champs product.template pour réutilisation lors des imports.
    """
    _name = "ftp.mapping.template"
    _description = "Template de mapping CSV"
    _order = "name"

    name = fields.Char(
        string="Nom du template",
        required=True,
        help="Nom descriptif du template de mapping (ex: 'Digital - Mapping complet')"
    )
    active = fields.Boolean(default=True)
    provider_id = fields.Many2one(
        "ftp.provider",
        string="Fournisseur",
        ondelete="cascade",
        help="Fournisseur associé à ce template de mapping"
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        default=lambda self: self.env.company.id,
    )
    line_ids = fields.One2many(
        "ftp.mapping.template.line",
        "template_id",
        string="Lignes de mapping",
        copy=True,
    )
    notes = fields.Text(
        string="Notes",
        help="Notes ou commentaires sur ce template de mapping"
    )
    line_count = fields.Integer(
        string="Nombre de mappings",
        compute="_compute_line_count",
        store=True,
    )

    @api.depends("line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    def action_view_lines(self):
        """Ouvre la vue des lignes de mapping."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Lignes de mapping"),
            "res_model": "ftp.mapping.template.line",
            "view_mode": "tree,form",
            "domain": [("template_id", "=", self.id)],
            "context": {"default_template_id": self.id},
        }

    def action_duplicate(self):
        """Duplique le template avec un nouveau nom."""
        self.ensure_one()
        new_template = self.copy({"name": _("%s (copie)") % self.name})
        return {
            "type": "ir.actions.act_window",
            "res_model": "ftp.mapping.template",
            "view_mode": "form",
            "res_id": new_template.id,
        }

    # =========================================================================
    # EXPORT / IMPORT JSON
    # =========================================================================
    def action_export_json(self):
        """Exporte le(s) template(s) sélectionné(s) en fichier JSON téléchargeable.
        
        Fonctionne pour un seul template (bouton form) ou plusieurs (action liste).
        Crée une pièce jointe temporaire et renvoie l'URL de téléchargement.
        """
        templates = self
        if not templates:
            raise UserError(_("Aucun template sélectionné pour l'export."))
        
        export_data = []
        for tmpl in templates:
            tmpl_data = {
                "name": tmpl.name,
                "provider_name": tmpl.provider_id.name if tmpl.provider_id else None,
                "notes": tmpl.notes or "",
                "lines": [],
            }
            for line in tmpl.line_ids:
                line_data = {
                    "sequence": line.sequence,
                    "source_column": line.source_column,
                    "target_field": line.target_field or "",
                    "target_field_names": [f.name for f in line.target_field_ids] if line.target_field_ids else [],
                    "transform_type": line.transform_type or "none",
                    "transform_value": line.transform_value or "",
                    "transform_value2": line.transform_value2 or "",
                    "concat_column": line.concat_column or "",
                    "concat_separator": line.concat_separator if line.concat_separator is not None else " ",
                    "required_field": line.required_field,
                    "skip_if_empty": line.skip_if_empty,
                    "active": line.active,
                    "notes": line.notes or "",
                }
                tmpl_data["lines"].append(line_data)
            export_data.append(tmpl_data)
        
        # Générer le JSON
        json_content = json.dumps(export_data, indent=2, ensure_ascii=False)
        b64_data = base64.b64encode(json_content.encode("utf-8"))
        
        # Nom du fichier
        if len(templates) == 1:
            safe_name = (templates.name or "template").replace(" ", "_").replace("/", "_")
            file_name = "mapping_%s.json" % safe_name
        else:
            file_name = "mapping_templates_%d.json" % len(templates)
        
        # Créer une pièce jointe pour le téléchargement
        attachment = self.env["ir.attachment"].sudo().create({
            "name": file_name,
            "type": "binary",
            "datas": b64_data,
            "mimetype": "application/json",
            "res_model": "ftp.mapping.template",
            "res_id": templates[0].id if len(templates) == 1 else 0,
        })
        
        # Retourner l'action de téléchargement
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%d?download=true&filename=%s" % (attachment.id, file_name),
            "target": "new",
        }

    @api.model
    def action_import_json(self, b64_data, filename=None, provider_id=None):
        """Importe un ou plusieurs templates depuis un fichier JSON (base64).
        
        Args:
            b64_data: Contenu du fichier JSON encodé en base64
            filename: Nom du fichier (optionnel)
            provider_id: ID du provider à assigner (optionnel, sinon cherche par nom)
            
        Returns:
            Liste des IDs des templates créés
        """
        if not b64_data:
            raise UserError(_("Aucun fichier fourni."))
        
        try:
            json_content = base64.b64decode(b64_data).decode("utf-8")
            data = json.loads(json_content)
        except Exception as e:
            raise UserError(_("Fichier JSON invalide: %s") % str(e))
        
        # Accepter un seul template (dict) ou une liste
        if isinstance(data, dict):
            data = [data]
        
        if not isinstance(data, list):
            raise UserError(_("Format JSON invalide: attendu une liste de templates."))
        
        FieldRegistry = self.env["ftp.mapping.field.registry"].sudo()
        created_ids = []
        
        for tmpl_data in data:
            if not isinstance(tmpl_data, dict):
                continue
            
            # Trouver le provider par ID ou par nom
            tmpl_provider_id = provider_id
            if not tmpl_provider_id and tmpl_data.get("provider_name"):
                provider = self.env["ftp.provider"].sudo().search(
                    [("name", "ilike", tmpl_data["provider_name"])], limit=1
                )
                if provider:
                    tmpl_provider_id = provider.id
            
            # Créer le template
            tmpl_vals = {
                "name": tmpl_data.get("name", _("Template importé")),
                "notes": tmpl_data.get("notes", ""),
            }
            if tmpl_provider_id:
                tmpl_vals["provider_id"] = tmpl_provider_id
            
            # Préparer les lignes
            line_vals_list = []
            for line_data in tmpl_data.get("lines", []):
                if not isinstance(line_data, dict):
                    continue
                
                target_field = line_data.get("target_field", "")
                
                # Chercher le field registry pour target_field_id
                target_field_id = False
                if target_field:
                    reg = FieldRegistry.search([("name", "=", target_field)], limit=1)
                    if reg:
                        target_field_id = reg.id
                
                # Chercher les field registries pour target_field_ids (Many2many)
                target_field_ids_cmd = []
                for fname in line_data.get("target_field_names", []):
                    reg = FieldRegistry.search([("name", "=", fname)], limit=1)
                    if reg:
                        target_field_ids_cmd.append(reg.id)
                
                line_vals = {
                    "sequence": line_data.get("sequence", 10),
                    "source_column": line_data.get("source_column", ""),
                    "target_field": target_field,
                    "target_field_id": target_field_id,
                    "transform_type": line_data.get("transform_type", "none"),
                    "transform_value": line_data.get("transform_value", ""),
                    "transform_value2": line_data.get("transform_value2", ""),
                    "concat_column": line_data.get("concat_column", ""),
                    "concat_separator": line_data.get("concat_separator", " "),
                    "required_field": line_data.get("required_field", False),
                    "skip_if_empty": line_data.get("skip_if_empty", True),
                    "active": line_data.get("active", True),
                    "notes": line_data.get("notes", ""),
                }
                if target_field_ids_cmd:
                    line_vals["target_field_ids"] = [(6, 0, target_field_ids_cmd)]
                
                line_vals_list.append((0, 0, line_vals))
            
            tmpl_vals["line_ids"] = line_vals_list
            
            new_tmpl = self.create(tmpl_vals)
            created_ids.append(new_tmpl.id)
            _logger.info("[MAPPING-IMPORT] Created template '%s' (id=%d) with %d lines",
                        new_tmpl.name, new_tmpl.id, len(line_vals_list))
        
        return created_ids

    def action_refresh_field_registry(self):
        """Rafraîchit le registre des champs disponibles pour le mapping.
        
        Scanne les modèles (product.template, product.odr, product.supplierinfo, etc.)
        et met à jour la liste des champs disponibles dans le sélecteur.
        """
        FieldRegistry = self.env["ftp.mapping.field.registry"].sudo()
        FieldRegistry._refresh_product_template_fields()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Registre rafraîchi"),
                "message": _("Les champs ODR, Supplierinfo et autres sont maintenant disponibles."),
                "type": "success",
                "sticky": False,
            },
        }


class FtpMappingTemplateLine(models.Model):
    """Ligne de mapping : colonne CSV → champ product.template."""
    _name = "ftp.mapping.template.line"
    _description = "Ligne de mapping CSV"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "ftp.mapping.template",
        string="Template",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    source_column = fields.Char(
        string="Colonne CSV",
        required=True,
        help="Nom exact de la colonne dans le fichier CSV (sensible à la casse)"
    )
    target_field_id = fields.Many2one(
        "ftp.mapping.field.registry",
        string="Champ Odoo (principal)",
        help="Tapez pour rechercher un champ par nom. Inclut product.template, supplierinfo, ODR, SOA...",
    )
    target_field = fields.Char(
        string="Nom technique du champ",
        help="Nom technique du champ cible (rempli automatiquement depuis le sélecteur)",
    )
    target_field_ids = fields.Many2many(
        "ftp.mapping.field.registry",
        "ftp_mapping_line_field_rel",
        "line_id",
        "field_id",
        string="Champs Odoo multiples",
        help="Sélectionnez plusieurs champs Odoo pour mapper la même colonne CSV vers plusieurs champs"
    )
    target_field_label = fields.Char(
        string="Libellé du champ",
        compute="_compute_target_field_label",
        store=True,
    )
    field_type = fields.Selection(
        selection=[
            ("char", "Texte"),
            ("text", "Texte long"),
            ("integer", "Entier"),
            ("float", "Décimal"),
            ("boolean", "Booléen"),
            ("date", "Date"),
            ("datetime", "Date/Heure"),
            ("selection", "Sélection"),
            ("many2one", "Relation"),
            ("html", "HTML"),
            ("binary", "Binaire"),
            ("other", "Autre"),
        ],
        string="Type de champ",
        compute="_compute_target_field_label",
        store=True,
    )
    transform_type = fields.Selection(
        selection=[
            ("none", "Aucune"),
            ("strip", "Supprimer espaces"),
            ("upper", "Majuscules"),
            ("lower", "Minuscules"),
            ("replace", "Remplacer"),
            ("divide", "Diviser par"),
            ("multiply", "Multiplier par"),
            ("default_if_empty", "Valeur par défaut si vide"),
            ("lookup", "Recherche (relation)"),
            ("concat", "Concaténer avec autre colonne"),
            ("extract_date_start", "Extraire date début (DD/MM-DD/MM)"),
            ("extract_date_end", "Extraire date fin (DD/MM-DD/MM)"),
        ],
        default="none",
        string="Transformation",
        help="Transformation à appliquer à la valeur avant import"
    )
    transform_value = fields.Char(
        string="Valeur transformation",
        help="Paramètre de la transformation (ex: diviseur, texte à remplacer, valeur par défaut)"
    )
    transform_value2 = fields.Char(
        string="Valeur 2",
        help="Second paramètre (ex: texte de remplacement)"
    )
    concat_column = fields.Char(
        string="Colonne à concaténer",
        help="Nom de la colonne CSV à concaténer. Ex: 'Colonne2' ou plusieurs séparées par ; : 'Col1;Col2'"
    )
    concat_separator = fields.Char(
        string="Séparateur de concaténation",
        default=" ",
        help="Séparateur entre les valeurs concaténées. Ex: ' ' ou ' - ' ou ' à compter de '"
    )
    required_field = fields.Boolean(
        string="Requis",
        default=False,
        help="Si coché, la ligne sera ignorée si cette colonne est vide"
    )
    skip_if_empty = fields.Boolean(
        string="Ignorer si vide",
        default=True,
        help="Ne pas écraser la valeur existante si la colonne CSV est vide"
    )
    active = fields.Boolean(default=True)
    notes = fields.Char(string="Notes")

    @api.onchange("target_field_id")
    def _onchange_target_field_id(self):
        """Synchronise target_field (Char) depuis target_field_id (Many2one).
        Permet la recherche via Many2one tout en gardant le nom technique en Char
        pour la compatibilité avec le moteur d'import.
        """
        for rec in self:
            if rec.target_field_id:
                rec.target_field = rec.target_field_id.name
            else:
                rec.target_field = False

    @api.model_create_multi
    def create(self, vals_list):
        """Override create pour synchroniser target_field_id depuis target_field si besoin.
        Gère la compatibilité: si target_field est défini mais pas target_field_id,
        cherche automatiquement le registre correspondant.
        """
        FieldRegistry = self.env["ftp.mapping.field.registry"].sudo()
        for vals in vals_list:
            # Si target_field_id est défini, synchroniser target_field
            if vals.get("target_field_id") and not vals.get("target_field"):
                reg = FieldRegistry.browse(vals["target_field_id"])
                if reg.exists():
                    vals["target_field"] = reg.name
            # Si target_field est défini mais pas target_field_id, chercher le registre
            elif vals.get("target_field") and not vals.get("target_field_id"):
                reg = FieldRegistry.search([("name", "=", vals["target_field"])], limit=1)
                if reg:
                    vals["target_field_id"] = reg.id
        return super().create(vals_list)

    def write(self, vals):
        """Override write pour synchroniser target_field ↔ target_field_id."""
        FieldRegistry = self.env["ftp.mapping.field.registry"].sudo()
        if "target_field_id" in vals and vals["target_field_id"] and "target_field" not in vals:
            reg = FieldRegistry.browse(vals["target_field_id"])
            if reg.exists():
                vals["target_field"] = reg.name
        elif "target_field" in vals and vals["target_field"] and "target_field_id" not in vals:
            reg = FieldRegistry.search([("name", "=", vals["target_field"])], limit=1)
            if reg:
                vals["target_field_id"] = reg.id
        return super().write(vals)

    @api.depends("target_field")
    def _compute_target_field_label(self):
        """Récupère le libellé et le type du champ cible."""
        ProductTemplate = self.env["product.template"]
        for rec in self:
            if rec.target_field and rec.target_field in ProductTemplate._fields:
                field_obj = ProductTemplate._fields[rec.target_field]
                rec.target_field_label = field_obj.string or rec.target_field
                ftype = field_obj.type
                if ftype in ("char", "text", "integer", "float", "boolean", "date", "datetime", "selection", "many2one", "html", "binary"):
                    rec.field_type = ftype
                else:
                    rec.field_type = "other"
            else:
                rec.target_field_label = rec.target_field or ""
                rec.field_type = "other"

    def apply_transform(self, value, row_data=None, header_index=None):
        """Applique la transformation configurée à une valeur.
        
        Args:
            value: Valeur brute du CSV (colonne source)
            row_data: Liste des valeurs de la ligne complète (pour concaténation)
            header_index: Dictionnaire {nom_colonne_lowercase: index} (pour concaténation)
            
        Returns:
            Valeur transformée
            
        Exemples de concaténation:
            - concat_column = "Description2" → concatène avec la colonne Description2
            - concat_column = "Col1;Col2;Col3" → concatène avec plusieurs colonnes
            - concat_separator = " - " → utilise " - " comme séparateur
            - concat_separator = " à compter de " → pour des dates "01/01/2025 à compter de 31/12/2025"
        """
        self.ensure_one()
        if value is None:
            value = ""
        
        # Conversion en string si nécessaire
        if not isinstance(value, str):
            value = str(value)
        
        transform = self.transform_type or "none"
        param1 = self.transform_value or ""
        param2 = self.transform_value2 or ""
        
        if transform == "none":
            return value
        elif transform == "strip":
            return value.strip()
        elif transform == "upper":
            return value.upper()
        elif transform == "lower":
            return value.lower()
        elif transform == "replace":
            return value.replace(param1, param2)
        elif transform == "divide":
            try:
                divisor = float(param1) if param1 else 1.0
                return float(value) / divisor if divisor != 0 else 0.0
            except (ValueError, TypeError):
                return value
        elif transform == "multiply":
            try:
                multiplier = float(param1) if param1 else 1.0
                return float(value) * multiplier
            except (ValueError, TypeError):
                return value
        elif transform == "default_if_empty":
            return value.strip() if value.strip() else param1
        elif transform == "lookup":
            # Recherche dans un modèle (param1 = model, param2 = field to search)
            # Retourne l'ID si trouvé
            return value  # À implémenter si besoin
        elif transform == "concat":
            # =====================================================
            # CONCATÉNATION avec une ou plusieurs autres colonnes
            # =====================================================
            # concat_column peut contenir:
            #   - "NomColonne" : une seule colonne
            #   - "Col1;Col2;Col3" : plusieurs colonnes séparées par ;
            # concat_separator : le séparateur à utiliser entre les valeurs
            #
            # Exemple: source="Date début", concat_column="Date fin", separator=" à "
            # Résultat: "01/01/2025 à 31/12/2025"
            # =====================================================
            if not row_data or not header_index:
                # Pas de données de ligne disponibles, retourner la valeur telle quelle
                _logger.warning("Concaténation demandée mais row_data ou header_index manquant")
                return value
            
            concat_cols = self.concat_column or ""
            separator = self.concat_separator if self.concat_separator is not None else " "
            
            # Parser les colonnes à concaténer (peuvent être séparées par ; ou ,)
            col_names = [c.strip() for c in concat_cols.replace(",", ";").split(";") if c.strip()]
            
            if not col_names:
                return value
            
            # Construire la liste des valeurs à concaténer
            values_to_concat = [value.strip()] if value.strip() else []
            
            for col_name in col_names:
                col_name_lower = col_name.lower()
                col_idx = header_index.get(col_name_lower)
                if col_idx is not None and col_idx < len(row_data):
                    col_value = (row_data[col_idx] or "").strip()
                    if col_value:
                        values_to_concat.append(col_value)
            
            # Joindre avec le séparateur
            result = separator.join(values_to_concat)
            return result
        elif transform == "extract_date_start":
            # =====================================================
            # EXTRACTION DE DATE DÉBUT depuis format "DD/MM-DD/MM"
            # =====================================================
            # Format attendu: "SELL OUT 07/01-03/02" ou "07/01-03/02"
            # Extrait: 07/01 (première date)
            # Ajoute automatiquement l'année courante: 07/01/2025
            # =====================================================
            import re
            from datetime import datetime
            
            if not value:
                return ""
            
            # Pattern pour DD/MM-DD/MM (avec ou sans espaces)
            pattern = r'(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})'
            match = re.search(pattern, value)
            
            if match:
                day_start = match.group(1).zfill(2)
                month_start = match.group(2).zfill(2)
                
                # Déterminer l'année (année courante par défaut)
                current_year = datetime.now().year
                
                # Si on est en décembre et la date de début est en janvier,
                # c'est probablement l'année suivante
                current_month = datetime.now().month
                start_month_int = int(month_start)
                
                if current_month == 12 and start_month_int == 1:
                    year = current_year + 1
                elif current_month == 1 and start_month_int == 12:
                    year = current_year - 1
                else:
                    year = current_year
                
                # Retourner au format DD/MM/YYYY
                result_date = f"{day_start}/{month_start}/{year}"
                _logger.debug("[EXTRACT_DATE_START] Extracted '%s' from '%s'", result_date, value)
                return result_date
            else:
                _logger.warning("[EXTRACT_DATE_START] No date pattern found in '%s'", value)
                return ""
        elif transform == "extract_date_end":
            # =====================================================
            # EXTRACTION DE DATE FIN depuis format "DD/MM-DD/MM"
            # =====================================================
            # Format attendu: "SELL OUT 07/01-03/02" ou "07/01-03/02"
            # Extrait: 03/02 (deuxième date)
            # Ajoute automatiquement l'année courante: 03/02/2025
            # =====================================================
            import re
            from datetime import datetime
            
            if not value:
                return ""
            
            # Pattern pour DD/MM-DD/MM (avec ou sans espaces)
            pattern = r'(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})'
            match = re.search(pattern, value)
            
            if match:
                day_end = match.group(3).zfill(2)
                month_end = match.group(4).zfill(2)
                
                # Déterminer l'année
                current_year = datetime.now().year
                current_month = datetime.now().month
                end_month_int = int(month_end)
                
                # Si on est en décembre et la date de fin est en janvier/février,
                # c'est probablement l'année suivante
                if current_month == 12 and end_month_int <= 2:
                    year = current_year + 1
                elif current_month == 1 and end_month_int == 12:
                    year = current_year - 1
                else:
                    year = current_year
                
                # Retourner au format DD/MM/YYYY
                result_date = f"{day_end}/{month_end}/{year}"
                _logger.debug("[EXTRACT_DATE_END] Extracted '%s' from '%s'", result_date, value)
                return result_date
            else:
                _logger.warning("[EXTRACT_DATE_END] No date pattern found in '%s'", value)
                return ""
        
        return value

    @api.model
    def _get_product_fields_selection(self):
        """Retourne la liste des champs pour le menu déroulant.
        
        Inclut les champs de:
        - product.template
        - product.product
        - product.supplierinfo
        - product.odr (ODR - offres de remboursement)
        
        IMPORTANT: Cette méthode doit toujours inclure les valeurs existantes
        dans la base de données pour éviter les erreurs JavaScript.
        """
        ProductTemplate = self.env["product.template"]
        ProductProduct = self.env["product.product"]
        SupplierInfo = self.env["product.supplierinfo"]
        
        # Collecter les valeurs existantes dans la base de données
        existing_values = set()
        try:
            self.env.cr.execute("""
                SELECT DISTINCT target_field 
                FROM ftp_mapping_template_line 
                WHERE target_field IS NOT NULL AND target_field != ''
            """)
            existing_values = {row[0] for row in self.env.cr.fetchall()}
        except Exception:
            pass
        
        # Collecter tous les champs disponibles
        all_fields = {}  # fname -> (label, source_model)
        
        # Champs de product.template
        for fname, field in ProductTemplate._fields.items():
            if fname.startswith("__") or fname in ("id", "create_uid", "create_date", "write_uid", "write_date", 
                                                     "display_name", "activity_ids", "message_ids", "website_message_ids"):
                continue
            if field.compute and not field.store:
                continue
            if field.type in ("one2many", "many2many"):
                continue
            all_fields[fname] = (f"{field.string or fname} ({fname})", "template")
        
        # Champs de product.product (ajoute ceux qui n'existent pas sur template)
        for fname, field in ProductProduct._fields.items():
            if fname in all_fields:
                continue
            if fname.startswith("__") or fname in ("id", "create_uid", "create_date", "write_uid", "write_date", 
                                                     "display_name", "activity_ids", "message_ids", "website_message_ids"):
                continue
            if field.compute and not field.store:
                continue
            if field.type in ("one2many", "many2many"):
                continue
            all_fields[fname] = (f"{field.string or fname} ({fname})", "product")
        
        # Champs de product.supplierinfo (comme supplier_stock)
        for fname, field in SupplierInfo._fields.items():
            if fname in all_fields:
                continue
            if fname.startswith("__") or fname in ("id", "create_uid", "create_date", "write_uid", "write_date", 
                                                     "display_name", "activity_ids", "message_ids", "website_message_ids"):
                continue
            if field.compute and not field.store:
                continue
            if field.type in ("one2many", "many2many"):
                continue
            all_fields[fname] = (f"📦 {field.string or fname} ({fname})", "supplierinfo")
        
        # Champs de product.odr (ODR - offres de remboursement)
        try:
            ProductOdr = self.env["product.odr"]
            for fname, field in ProductOdr._fields.items():
                if fname in all_fields:
                    continue
                if fname.startswith("__") or fname in ("id", "create_uid", "create_date", "write_uid", "write_date", 
                                                         "display_name", "activity_ids", "message_ids", "website_message_ids"):
                    continue
                if field.compute and not field.store:
                    continue
                if field.type in ("one2many", "many2many"):
                    continue
                all_fields[fname] = (f"🎁 {field.string or fname} ({fname}) [ODR]", "odr")
        except Exception:
            pass  # product.odr n'est peut-être pas installé
        
        # Champs de soa.budget.line (SOA - budgets)
        try:
            SoaBudgetLine = self.env["soa.budget.line"]
            for fname, field in SoaBudgetLine._fields.items():
                if fname in all_fields:
                    continue
                if fname.startswith("__") or fname in ("id", "create_uid", "create_date", "write_uid", "write_date", 
                                                         "display_name", "activity_ids", "message_ids", "website_message_ids"):
                    continue
                if field.compute and not field.store:
                    continue
                if field.type in ("one2many", "many2many"):
                    continue
                all_fields[fname] = (f"💰 {field.string or fname} ({fname}) [SOA]", "soa")
        except Exception:
            pass  # soa.budget.line n'est peut-être pas installé
        
        # Construire le résultat avec champs prioritaires en premier
        result = [
            ('name', '📝 Nom (name)'),
            ('default_code', '🔢 Référence interne (default_code)'),
            ('barcode', '📊 Code-barres (barcode)'),
            ('list_price', '💰 Prix de vente (list_price)'),
            ('standard_price', '💵 Coût (standard_price)'),
            ('description', '📄 Description (description)'),
            ('categ_id', '📂 Catégorie (categ_id)'),
            ('product_brand_id', '🏷️ Marque (product_brand_id)'),
            # === Champs Fournisseur (product.supplierinfo) ===
            ('pvgc', '📦 PVGC TTC (pvgc) [Fournisseur]'),
            ('price', '📦 Price (price) [Fournisseur]'),
            ('supplier_stock', '📦 Stock fournisseur (supplier_stock) [Fournisseur]'),
            ('product_cost', '📦 Prix net calculé (product_cost) [Fournisseur]'),
            ('delay', '📦 Délai livraison (delay) [Fournisseur]'),
            ('min_qty', '📦 Qté minimum (min_qty) [Fournisseur]'),
            ('discount', '📦 Remise % (discount) [Fournisseur]'),
        ]
        priority_fields = {r[0] for r in result}
        
        # Ajouter les autres champs triés
        other_fields = [(k, v[0]) for k, v in all_fields.items() if k not in priority_fields]
        other_fields.sort(key=lambda x: x[1])
        result.extend(other_fields)
        
        # Ajouter les valeurs existantes qui ne sont pas dans les modèles connus
        result_field_names = {r[0] for r in result}
        for existing_val in existing_values:
            if existing_val and existing_val not in result_field_names:
                # Ajouter sans avertissement - juste le nom technique
                result.append((existing_val, f"{existing_val}"))
        
        return result
    
    @api.model
    def get_product_template_fields(self):
        """Retourne la liste des champs product.template disponibles pour le mapping (format dict)."""
        ProductTemplate = self.env["product.template"]
        fields_list = []
        for fname, field in ProductTemplate._fields.items():
            # Exclure certains champs système
            if fname.startswith("__") or fname in ("id", "create_uid", "create_date", "write_uid", "write_date"):
                continue
            # Exclure les champs computed non stockés et les one2many/many2many
            if field.compute and not field.store:
                continue
            if field.type in ("one2many", "many2many"):
                continue
            fields_list.append({
                "name": fname,
                "string": field.string or fname,
                "type": field.type,
                "required": field.required,
            })
        return sorted(fields_list, key=lambda x: x["string"])
