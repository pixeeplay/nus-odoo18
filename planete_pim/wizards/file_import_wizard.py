# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json


class PlanetePimFileImportWizard(models.TransientModel):
    _name = "planete.pim.file.import.wizard"
    _description = "Planète PIM - File Import Wizard"

    file = fields.Binary(string="Fichier", required=True)
    file_name = fields.Char(string="Nom du fichier")
    has_header = fields.Boolean(string="En-tête", default=True)
    encoding = fields.Char(string="Encodage (optionnel)")
    delimiter = fields.Char(
        string="Délimiteur (optionnel)",
        size=5,
        help="Délimiteur CSV (1 à 5 caractères). Ex: ',', ';', '|', '\\t', 'I', '||'"
    )
    # Scripts (onglet dédié dans la vue). Le premier est utilisé par défaut.
    script_default = fields.Text(
        string="Script par défaut",
        default=(
            "# PIM Script v1\n"
            "# Règles disponibles (True/False):\n"
            "# ENABLE_NORMALIZE_EAN, ENABLE_CLEAR_DUP_BARCODES, ENABLE_DEDUP_IDENTICAL_ROWS, ENABLE_VALIDATE, ENABLE_REF_MODIFIED_COL\n"
            "ENABLE_NORMALIZE_EAN=True\n"
            "ENABLE_CLEAR_DUP_BARCODES=True\n"
            "ENABLE_DEDUP_IDENTICAL_ROWS=True\n"
            "ENABLE_VALIDATE=True\n"
            "ENABLE_REF_MODIFIED_COL=True\n"
        ),
        help="Script par défaut appliqué lors de l'import (texte éditable)."
    )
    script_2 = fields.Text(
        string="Script 2",
        default=(
            "# PIM Script v1\n"
            "ENABLE_NORMALIZE_EAN=True\n"
            "ENABLE_CLEAR_DUP_BARCODES=True\n"
            "ENABLE_DEDUP_IDENTICAL_ROWS=True\n"
            "ENABLE_VALIDATE=True\n"
            "ENABLE_REF_MODIFIED_COL=True\n"
        ),
        help="Script alternatif (texte éditable)."
    )

    # Source
    provider_id = fields.Many2one("ftp.provider", string="Provider", required=True)
    supplier_id = fields.Many2one(
        "res.partner",
        string="Fournisseur (optionnel)",
        help="Par défaut dérivé du Provider (son partenaire). Vous pouvez le surcharger."
    )
    
    # Template de mapping (optionnel)
    mapping_template_id = fields.Many2one(
        "ftp.mapping.template",
        string="Template de mapping",
        help="Sélectionnez un template de mapping pour définir comment les colonnes du fichier "
             "correspondent aux champs Odoo. Si vide, le mapping par défaut sera utilisé.\n\n"
             "💡 Le template permet de mapper des colonnes CSV vers les champs produit, "
             "avec des transformations (concat, uppercase, etc.)."
    )
    
    # Mode d'import
    import_mode = fields.Selection(
        [
            ("standard", "Standard - Import fichier"),
            ("full", "FULL - Création produits"),
            ("delta", "DELTA - Prix/Stock uniquement"),
            ("refresh_content", "REFRESH - Contenu produits"),
        ],
        string="Mode d'import",
        default="standard",
        required=True,
        help="Standard: Import classique depuis fichier\n"
             "FULL: Création de nouveaux produits (avec analyse doublons)\n"
             "DELTA: Mise à jour prix/stock uniquement (rapide)\n"
             "REFRESH: Mise à jour du contenu des produits existants"
    )

    # --- NUL sanitization helpers ---
    @api.model
    def _strip_nul(self, s):
        try:
            return ("" if s is None else str(s)).replace("\x00", "")
        except Exception:
            return s

    def _sanitize_vals(self, vals):
        vals = dict(vals or {})
        for k, v in list(vals.items()):
            field = self._fields.get(k)
            if field and isinstance(v, str) and field.type in ("char", "text", "html", "selection"):
                vals[k] = self._strip_nul(v)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._sanitize_vals(vals) for vals in vals_list]
        return super(PlanetePimFileImportWizard, self).create(vals_list)

    def write(self, vals):
        vals = self._sanitize_vals(vals)
        return super(PlanetePimFileImportWizard, self).write(vals)

    @api.onchange("provider_id")
    def _onchange_provider_id(self):
        """Pré-remplit supplier_id et mapping_template_id à partir du provider sélectionné."""
        if self.provider_id:
            partner = self.provider_id.partner_id
            self.supplier_id = partner.id if partner else False
            # Pré-remplir le template de mapping si le provider en a un
            if self.provider_id.mapping_template_id:
                self.mapping_template_id = self.provider_id.mapping_template_id.id
        else:
            self.supplier_id = False
            self.mapping_template_id = False

    def _sanitize_self(self):
        """Ensure current wizard record has no NULs before any DB flush."""
        self.ensure_one()
        dirty = {}
        for fname in ("file_name", "encoding", "delimiter", "script_default", "script_2"):
            val = getattr(self, fname)
            if isinstance(val, str):
                clean = self._strip_nul(val)
                if clean != val:
                    dirty[fname] = clean
        if dirty:
            self.write(dirty)

    # Options (déjà pris en compte dans l'API, implémentation d'écriture à venir)
    create_brands = fields.Boolean(string="Créer marques si manquantes", default=False)
    create_categories = fields.Boolean(string="Créer catégories si manquantes", default=False)
    clear_duplicate_barcodes = fields.Boolean(string="Purger CB dupliqués", default=False)
    update_stock = fields.Boolean(string="Mettre à jour le stock", default=False)
    write_direct = fields.Boolean(
        string="Créer directement les produits",
        default=False,
        help="Si activé, l'import écrit directement les produits (pas de staging). Utilisez Prévisualiser pour contrôler avant écriture."
    )

    def _build_options(self):
        self.ensure_one()
        options = {
            "has_header": bool(self.has_header),
            "encoding": (self._strip_nul((self.encoding or "").strip()) or None),
            "delimiter": (self._strip_nul((self.delimiter or "").strip()) or None),
            "create_brands": bool(self.create_brands),
            "create_categories": bool(self.create_categories),
            "clear_duplicate_barcodes": bool(self.clear_duplicate_barcodes),
            "update_stock": bool(self.update_stock),
            "do_write": bool(self.write_direct),
            # Source
            "provider_id": self.provider_id.id if self.provider_id else None,
            "supplier_id": self.supplier_id.id if self.supplier_id else None,
            # Template de mapping
            "mapping_template_id": self.mapping_template_id.id if self.mapping_template_id else None,
            # Scripts pass-through
            "script_default": self._strip_nul(self.script_default or ""),
            "script_2": self._strip_nul(self.script_2 or ""),
        }
        
        # Si un template de mapping est sélectionné, construire le mapping complet
        # avec les informations de transformation (concat, replace, etc.)
        if self.mapping_template_id:
            mapping = {}
            mapping_lines = []  # Liste détaillée des lignes avec transformations
            
            for line in self.mapping_template_id.line_ids.filtered(lambda l: l.active):
                field_key = line.target_field
                source_col = line.source_column.strip().lower() if line.source_column else ""
                
                if field_key and source_col:
                    # Ajouter au mapping simple (plusieurs colonnes peuvent pointer vers le même champ)
                    if field_key not in mapping:
                        mapping[field_key] = [source_col]
                    else:
                        mapping[field_key].append(source_col)
                    
                    # Ajouter les détails de transformation pour cette ligne
                    line_info = {
                        "source_column": source_col,
                        "target_field": field_key,
                        "transform_type": line.transform_type or "none",
                        "transform_value": line.transform_value or "",
                        "transform_value2": line.transform_value2 or "",
                        "concat_column": line.concat_column or "",
                        "concat_separator": line.concat_separator if line.concat_separator is not None else " ",
                        "skip_if_empty": line.skip_if_empty,
                        "required_field": line.required_field,
                    }
                    mapping_lines.append(line_info)
            
            options["mapping"] = mapping
            options["mapping_lines"] = mapping_lines  # Détails complets pour transformations
            
            # LOG CRITIQUE: Afficher le mapping construit pour debug
            import logging
            _logger = logging.getLogger(__name__)
            _logger.info("[WIZARD-MAPPING] Template: %s (id=%s)", 
                        self.mapping_template_id.name, self.mapping_template_id.id)
            _logger.info("[WIZARD-MAPPING] Lignes actives: %d", len(mapping_lines))
            _logger.info("[WIZARD-MAPPING] Mapping construit: %s", mapping)
            _logger.info("[WIZARD-MAPPING] Champs mappés: %s", list(mapping.keys()))
        else:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning("[WIZARD-MAPPING] AUCUN TEMPLATE DE MAPPING SÉLECTIONNÉ!")
        
        return options

    def _ensure_file(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_("Veuillez sélectionner un fichier à importer."))
    
    def _ensure_mapping_template(self):
        """Vérifie qu'un template de mapping est sélectionné.
        
        RÈGLE CRITIQUE: Sans template de mapping, on ne peut pas savoir comment
        mapper les colonnes du fichier vers les champs Odoo.
        L'import est donc INTERDIT sans template.
        """
        self.ensure_one()
        if not self.mapping_template_id:
            raise UserError(_(
                "❌ Import bloqué: Aucun template de mapping sélectionné!\n\n"
                "📋 Un template de mapping est OBLIGATOIRE pour importer des produits.\n\n"
                "💡 Solutions:\n"
                "1. Sélectionnez un template de mapping dans la liste déroulante\n"
                "2. Ou configurez un template par défaut sur le Provider '%s'\n"
                "   → Aller dans le Provider → Onglet PIM → Champ 'Template de mapping'\n\n"
                "🔧 Si vous n'avez pas de template, créez-en un dans:\n"
                "   Planification → Configuration → Templates de mapping"
            ) % (self.provider_id.name if self.provider_id else "N/A"))

    def _run_import(self):
        self._ensure_file()
        self._ensure_mapping_template()  # RÈGLE: Template obligatoire
        self._sanitize_self()
        options = self._build_options()
        # Forcer la prévisualisation en mode validation/staging uniquement (pas d'écriture produits)
        options["do_write"] = False
        # Délègue au moteur d'import PIM (crée un log, attache le fichier, effectue validations et preview)
        action = self.env["planete.pim.importer"].import_from_binary(
            self.file, self._strip_nul(self.file_name or "upload.csv"), options=options
        )
        return action

    def action_preview(self):
        # Pour cette première étape, la prévisualisation ouvre le log (avec aperçu + validations)
        return self._run_import()

    def action_import(self):
        # Lance un job asynchrone pour éviter de bloquer l'UI (gros fichiers)
        self._ensure_file()
        self._ensure_mapping_template()  # RÈGLE: Template obligatoire
        self._sanitize_self()
        options = self._build_options()
        job = self.env["planete.pim.import.job"].create({
            "name": _("Import PIM - %s") % (self._strip_nul(self.file_name or "upload.csv")),
            "import_mode": self.import_mode or "standard",
            "provider_id": self.provider_id.id if self.provider_id else False,
            "file_data": self.file,
            "file_data_name": self._strip_nul(self.file_name or "upload.csv"),
            "options_json": json.dumps(options or {}),
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Import en arrière-plan lancé"),
                "message": _("Le job #%s a été créé. Vous pouvez fermer cette fenêtre et consulter les Journaux d'import une fois terminé.") % (job.id,),
                "sticky": False,
            },
        }
