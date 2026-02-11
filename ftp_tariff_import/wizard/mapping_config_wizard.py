# -*- coding: utf-8 -*-
import base64
import csv
import io
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MappingConfigWizard(models.TransientModel):
    """Wizard pour configurer le mapping à partir d'un fichier CSV exemple."""
    _name = "ftp.mapping.config.wizard"
    _description = "Assistant de configuration du mapping CSV"

    provider_id = fields.Many2one(
        "ftp.provider",
        string="Fournisseur",
        required=True,
    )
    template_id = fields.Many2one(
        "ftp.mapping.template",
        string="Template existant",
        help="Sélectionner un template existant pour le modifier, ou laisser vide pour en créer un nouveau."
    )
    template_name = fields.Char(
        string="Nom du nouveau template",
        help="Nom du template si création d'un nouveau."
    )
    
    # Fichier CSV
    csv_file = fields.Binary(string="Fichier CSV exemple", required=True)
    csv_filename = fields.Char(string="Nom du fichier")
    
    # Paramètres CSV (héritées du provider)
    csv_encoding = fields.Selection(
        selection=[("auto", "Auto-detect"), ("utf-8", "UTF-8"), ("utf-8-sig", "UTF-8 with BOM"), ("cp1252", "Windows-1252"), ("latin-1", "Latin-1")],
        default="utf-8",
        string="Encodage",
    )
    csv_delimiter = fields.Selection(
        selection=[
            (";", "; (point-virgule)"),
            (",", ", (virgule)"),
            ("\\t", "Tabulation (\\t)"),
            ("|", "| (pipe)"),
            (" ", "Espace"),
            ("auto", "Auto-detect"),
            ("custom", "Personnalisé..."),
        ],
        default=";",
        string="Délimiteur",
    )
    csv_delimiter_custom = fields.Char(
        string="Délimiteur personnalisé",
        help="Saisissez un délimiteur personnalisé (1 à 5 caractères). Ex: '||', '::'"
    )
    
    # Colonnes détectées
    column_ids = fields.One2many(
        "ftp.mapping.config.wizard.column",
        "wizard_id",
        string="Colonnes détectées",
    )
    
    # Filtre de recherche
    search_filter = fields.Char(
        string="🔍 Rechercher",
        help="Filtrer les colonnes par nom ou champ Odoo",
    )
    
    state = fields.Selection([
        ('upload', 'Charger fichier'),
        ('mapping', 'Configurer mapping'),
    ], default='upload', string="Étape")

    @api.onchange('search_filter')
    def _onchange_search_filter(self):
        """Mettre à jour la visibilité des colonnes selon le filtre."""
        if not self.search_filter:
            # Tout afficher
            for col in self.column_ids:
                col.visible = True
        else:
            search = self.search_filter.lower()
            for col in self.column_ids:
                col.visible = (
                    search in (col.source_column or '').lower() or
                    search in (col.target_field or '').lower() or
                    search in (col.target_field_label or '').lower() or
                    search in (col.sample_value or '').lower()
                )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        provider_id = self.env.context.get('default_provider_id')
        if provider_id:
            provider = self.env['ftp.provider'].browse(provider_id)
            res['csv_encoding'] = provider.csv_encoding or 'utf-8'
            res['csv_delimiter'] = provider.csv_delimiter or ';'
        return res

    def action_analyze_file(self):
        """Analyser le fichier CSV et détecter les colonnes."""
        self.ensure_one()
        if not self.csv_file:
            raise UserError(_("Veuillez charger un fichier CSV."))
        
        # Décoder le fichier
        try:
            content = base64.b64decode(self.csv_file)
        except Exception as e:
            raise UserError(_("Impossible de décoder le fichier: %s") % str(e))
        
        # Détecter l'encodage
        encoding = self.csv_encoding
        if encoding == 'auto':
            for enc in ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1']:
                try:
                    content.decode(enc)
                    encoding = enc
                    break
                except Exception:
                    continue
            else:
                encoding = 'utf-8'
        
        # Résoudre le délimiteur effectif
        effective_delimiter = self.csv_delimiter or ';'
        if effective_delimiter == '\\t':
            effective_delimiter = '\t'
        elif effective_delimiter == 'custom':
            effective_delimiter = self.csv_delimiter_custom or ';'
        elif effective_delimiter == 'auto':
            effective_delimiter = None  # Sera détecté automatiquement
        
        # Lire le CSV
        try:
            text = content.decode(encoding)
            
            # Auto-détection du délimiteur si nécessaire
            if effective_delimiter is None:
                effective_delimiter = self._detect_delimiter(text)
                _logger.info("[MAPPING-WIZARD] Auto-detected delimiter: %r", effective_delimiter)
            
            reader = csv.reader(io.StringIO(text), delimiter=effective_delimiter)
            headers = next(reader, [])
            
            # Si on n'a qu'une seule colonne, tenter la détection automatique en fallback
            if len(headers) <= 1 and effective_delimiter != '\t':
                _logger.info("[MAPPING-WIZARD] Only 1 column detected with delimiter %r, trying auto-detect...", effective_delimiter)
                auto_delim = self._detect_delimiter(text)
                if auto_delim != effective_delimiter:
                    reader2 = csv.reader(io.StringIO(text), delimiter=auto_delim)
                    headers2 = next(reader2, [])
                    if len(headers2) > len(headers):
                        _logger.info("[MAPPING-WIZARD] Auto-detect found %d columns with delimiter %r (was %d with %r)",
                                    len(headers2), auto_delim, len(headers), effective_delimiter)
                        headers = headers2
                        effective_delimiter = auto_delim
                        # Re-create reader with correct delimiter
                        reader = csv.reader(io.StringIO(text), delimiter=effective_delimiter)
                        next(reader)  # Skip header
        except Exception as e:
            raise UserError(_("Erreur de lecture du CSV: %s") % str(e))
        
        if not headers:
            raise UserError(_("Aucune colonne détectée dans le fichier."))
        
        # Lire quelques lignes pour les valeurs d'exemple
        sample_rows = []
        for i, row in enumerate(reader):
            if i >= 3:
                break
            sample_rows.append(row)
        
        # Rafraîchir le registre des champs product.template
        FieldRegistry = self.env['ftp.mapping.field.registry']
        FieldRegistry._refresh_product_template_fields()
        
        # Récupérer les champs product.template disponibles
        product_fields = self._get_product_template_fields()
        
        # Si un template est sélectionné, construire un dict de mapping existant
        template_mapping = {}
        if self.template_id:
            for line in self.template_id.line_ids:
                template_mapping[line.source_column] = {
                    'target_field': line.target_field,
                    'transform_type': line.transform_type or 'strip',
                    'concat_column': line.concat_column,
                    'concat_separator': line.concat_separator,
                }
        
        # Créer les lignes de colonnes
        self.column_ids.unlink()
        column_vals = []
        for idx, header in enumerate(headers):
            header_clean = header.strip()
            # Exemple de valeur
            sample_value = ""
            for row in sample_rows:
                if idx < len(row) and row[idx].strip():
                    sample_value = row[idx].strip()[:100]
                    break
            
            # Vérifier d'abord si la colonne existe dans le template sélectionné
            suggested_field_id = False
            transform_type = 'strip'
            concat_column = ''
            concat_separator = ' '
            include = False
            
            if header_clean in template_mapping:
                # Utiliser le mapping du template existant
                mapping_info = template_mapping[header_clean]
                field_rec = FieldRegistry.search([('name', '=', mapping_info['target_field'])], limit=1)
                if field_rec:
                    suggested_field_id = field_rec.id
                    include = True
                transform_type = mapping_info.get('transform_type') or 'strip'
                concat_column = mapping_info.get('concat_column') or ''
                concat_separator = mapping_info.get('concat_separator') or ' '
            else:
                # Deviner le champ cible
                suggested_field_name = self._guess_target_field(header_clean, product_fields)
                if suggested_field_name:
                    field_rec = FieldRegistry.search([('name', '=', suggested_field_name), ('model_name', '=', 'product.template')], limit=1)
                    if field_rec:
                        suggested_field_id = field_rec.id
                        include = True
            
            column_vals.append({
                'wizard_id': self.id,
                'sequence': idx + 1,
                'source_column': header_clean,
                'sample_value': sample_value,
                'target_field_id': suggested_field_id,
                'include': include,
                'transform_type': transform_type,
                'concat_column': concat_column,
                'concat_separator': concat_separator,
            })
        
        self.env['ftp.mapping.config.wizard.column'].create(column_vals)
        
        # Passer à l'étape mapping
        self.state = 'mapping'
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _get_product_template_fields(self):
        """Récupérer les champs product.template disponibles."""
        ProductTemplate = self.env['product.template']
        fields_info = ProductTemplate.fields_get()
        result = {}
        
        # Types de champs autorisés
        allowed_types = ('char', 'text', 'integer', 'float', 'monetary', 'boolean', 'selection', 'many2one', 'date', 'datetime', 'html')
        
        for fname, finfo in fields_info.items():
            if finfo.get('type') in allowed_types and not finfo.get('readonly'):
                result[fname] = {
                    'string': finfo.get('string', fname),
                    'type': finfo.get('type'),
                }
        return result

    def _guess_target_field(self, column_name, product_fields):
        """Deviner le champ cible basé sur le nom de colonne."""
        col_lower = column_name.lower().replace(' ', '_').replace('-', '_')
        
        # Mapping direct
        mappings = {
            'name': 'name',
            'nom': 'name',
            'description': 'description',
            'barcode': 'barcode',
            'ean': 'barcode',
            'ean13': 'barcode',
            'code_barre': 'barcode',
            'ref': 'default_code',
            'reference': 'default_code',
            'sku': 'default_code',
            'default_code': 'default_code',
            'prix': 'list_price',
            'price': 'list_price',
            'prix_de_vente': 'list_price',
            'list_price': 'list_price',
            'cout': 'standard_price',
            'cost': 'standard_price',
            'prix_achat': 'standard_price',
            'standard_price': 'standard_price',
            'poids': 'weight',
            'weight': 'weight',
            'volume': 'volume',
            'type': 'detailed_type',
        }
        
        for key, field in mappings.items():
            if key in col_lower and field in product_fields:
                return field
        
        # Recherche par correspondance partielle dans les champs personnalisés (x_)
        for fname in product_fields:
            if fname.startswith('x_'):
                field_clean = fname[2:].lower()
                if field_clean in col_lower or col_lower in field_clean:
                    return fname
        
        return False

    def action_create_template(self):
        """Créer ou mettre à jour le template avec le mapping configuré."""
        self.ensure_one()
        
        # Vérifier qu'on a un template existant ou un nom pour en créer un nouveau
        if not self.template_id and not self.template_name:
            raise UserError(_("Veuillez sélectionner un template existant ou donner un nom pour en créer un nouveau."))
        
        # Récupérer les colonnes à inclure
        # CORRECTION: Accepter les lignes avec target_field OU target_field_id (permet concat sans champ principal)
        columns_to_map = self.column_ids.filtered(lambda c: c.include and (c.target_field or c.target_field_id))
        
        if not columns_to_map:
            raise UserError(_("Veuillez sélectionner au moins une colonne à mapper (cochez 'Inclure' et sélectionnez un champ Odoo)."))
        
        # Créer ou récupérer le template
        if self.template_id:
            template = self.template_id
            # Vider les lignes existantes
            template.line_ids.unlink()
        else:
            template = self.env['ftp.mapping.template'].create({
                'name': self.template_name,
                'provider_id': self.provider_id.id,
            })
        
        # Créer les lignes de mapping
        for col in columns_to_map:
            line_vals = {
                'template_id': template.id,
                'sequence': col.sequence,
                'source_column': col.source_column,
                'target_field': col.target_field,
                'transform_type': col.transform_type,
            }
            # Ajouter les champs de concaténation si applicable
            if col.transform_type == 'concat':
                line_vals['concat_column'] = col.concat_column
                line_vals['concat_separator'] = col.concat_separator or ' '
            self.env['ftp.mapping.template.line'].create(line_vals)
        
        # Assigner le template au provider si pas déjà fait
        if not self.provider_id.mapping_template_id:
            self.provider_id.mapping_template_id = template.id
        
        # Afficher un message de succès
        message = _('Le template "%s" a été créé avec %d colonnes mappées.') % (template.name, len(columns_to_map))
        
        # Retourner l'action d'ouverture du template
        return {
            'type': 'ir.actions.act_window',
            'name': _('Template de mapping'),
            'res_model': 'ftp.mapping.template',
            'res_id': template.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_message': message,
            },
        }

    def _detect_delimiter(self, text):
        """Détection automatique du délimiteur CSV à partir d'un échantillon de texte.
        
        Teste les délimiteurs courants (tabulation, ;, ,, |, espace) et retourne
        celui qui donne le meilleur score (plus de colonnes + consistance entre lignes).
        """
        if not text:
            return ';'
        
        lines = text.split('\n')[:10]
        if not lines:
            return ';'
        
        # Délimiteurs à tester, par ordre de préférence
        candidates = ['\t', ';', ',', '|', ' ']
        best_delimiter = ';'
        best_score = 0
        
        for delim in candidates:
            try:
                col_counts = []
                for line in lines:
                    if line.strip():
                        reader = csv.reader(io.StringIO(line), delimiter=delim, quotechar='"')
                        try:
                            row = next(reader)
                            col_counts.append(len(row))
                        except StopIteration:
                            col_counts.append(0)
                
                if not col_counts:
                    continue
                
                avg_cols = sum(col_counts) / len(col_counts)
                # Consistance: toutes les lignes ont le même nombre de colonnes
                consistency = 1.0 - (max(col_counts) - min(col_counts)) / max(max(col_counts), 1)
                # Bonus pour tabulation (souvent le bon choix pour .txt)
                bonus = 1.2 if delim == '\t' else 1.0
                score = avg_cols * consistency * bonus
                
                if score > best_score and avg_cols > 1:
                    best_score = score
                    best_delimiter = delim
            except Exception:
                continue
        
        _logger.info("[MAPPING-WIZARD] _detect_delimiter: best=%r (score=%.2f)", best_delimiter, best_score)
        return best_delimiter

    def action_back(self):
        """Retourner à l'étape précédente."""
        self.ensure_one()
        self.state = 'upload'
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class MappingFieldRegistry(models.Model):
    """Registre des champs product.template disponibles pour le mapping.
    Ce modèle technique permet d'utiliser un Many2one avec recherche native
    au lieu d'un Selection qui ne supporte pas la recherche.
    """
    _name = "ftp.mapping.field.registry"
    _description = "Registre des champs pour mapping"
    _order = "name"
    _rec_name = "display_name"

    name = fields.Char(string="Nom technique", required=True, index=True)
    display_name = fields.Char(string="Libellé", compute="_compute_display_name", store=True)
    field_label = fields.Char(string="Libellé du champ")
    field_type = fields.Char(string="Type")
    model_name = fields.Char(string="Modèle", default="product.template")

    _sql_constraints = [
        ('name_model_uniq', 'unique(name, model_name)', 'Le champ doit être unique par modèle!')
    ]

    @api.depends('name', 'field_label')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.field_label} ({rec.name})" if rec.field_label else rec.name

    @api.model
    def _refresh_product_template_fields(self):
        """Met à jour le registre avec tous les champs product.template et modèles liés."""
        # Types de champs autorisés
        allowed_types = ('char', 'text', 'integer', 'float', 'monetary', 'boolean', 'selection', 'many2one', 'date', 'datetime', 'html')
        
        # Champs à toujours inclure même s'ils sont "readonly" dans fields_get
        force_include = {'default_code', 'barcode', 'name', 'list_price', 'standard_price', 'weight', 'volume', 'description', 'description_sale', 'categ_id', 'type', 'detailed_type', 'uom_id', 'uom_po_id', 'price', 'date_start', 'date_end', 'amount', 'date_begin', 'soa_date_start', 'soa_date_end', 'soa_amount'}
        
        # Modèles à scanner pour les champs de mapping
        models_to_scan = [
            ('product.template', 'product.template'),
        ]
        
        # Modèles additionnels si disponibles
        additional_models = [
            ('product.supplierinfo', 'product.supplierinfo'),
            ('product.odr', 'product.odr'),
            ('product.ecotaxe.wizard', 'product.ecotaxe.wizard'),
            ('soa.budget.line', 'soa.budget.line'),
        ]
        
        for model_name, label in additional_models:
            try:
                if model_name in self.env:
                    models_to_scan.append((model_name, label))
            except Exception:
                pass
        
        to_create = []
        
        for model_name, model_label in models_to_scan:
            try:
                Model = self.env[model_name]
                fields_info = Model.fields_get()
            except Exception:
                _logger.debug("[MappingFieldRegistry] Model %s not available, skipping", model_name)
                continue
            
            existing = {r.name: r for r in self.search([('model_name', '=', model_name)])}
            
            for fname, finfo in fields_info.items():
                ftype = finfo.get('type', '')
                is_readonly = finfo.get('readonly', False)
                
                # Inclure si: type autorisé ET (pas readonly OU dans force_include)
                if ftype in allowed_types and (not is_readonly or fname in force_include):
                    display_label = finfo.get('string', fname)
                    # Préfixer avec le nom du modèle pour les modèles autres que product.template
                    if model_name != 'product.template':
                        display_label = f"{display_label} ({model_label})"
                    
                    if fname not in existing:
                        to_create.append({
                            'name': fname,
                            'field_label': display_label,
                            'field_type': ftype,
                            'model_name': model_name,
                        })
                    else:
                        # Mettre à jour le libellé si nécessaire
                        if existing[fname].field_label != display_label:
                            existing[fname].write({'field_label': display_label})
        
        if to_create:
            self.create(to_create)
            _logger.info("[MappingFieldRegistry] Created %d field entries total", len(to_create))
        
        return True

    @api.model
    def _name_search(self, name='', domain=None, operator='ilike', limit=100, order=None):
        """Recherche par nom technique OU libellé."""
        domain = domain or []
        if name:
            domain = ['|', ('name', operator, name), ('field_label', operator, name)] + domain
        return self._search(domain, limit=limit, order=order)


class MappingConfigWizardColumn(models.TransientModel):
    """Ligne de colonne détectée dans le wizard."""
    _name = "ftp.mapping.config.wizard.column"
    _description = "Colonne détectée (wizard)"
    _order = "sequence"

    wizard_id = fields.Many2one(
        "ftp.mapping.config.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    visible = fields.Boolean(string="Visible", default=True, help="Utilisé pour le filtre de recherche")
    source_column = fields.Char(string="Colonne CSV", readonly=True)
    sample_value = fields.Char(string="Exemple", readonly=True)
    
    # Remplacé Selection par Many2one pour avoir la recherche native
    target_field_id = fields.Many2one(
        "ftp.mapping.field.registry",
        string="Champ Odoo",
        help="Sélectionnez le champ correspondant. Tapez pour rechercher (product.template, product.supplierinfo, product.odr...).",
    )
    target_field = fields.Char(
        string="Nom technique",
        related="target_field_id.name",
        store=True,
    )
    target_field_label = fields.Char(
        string="Libellé champ",
        related="target_field_id.field_label",
        store=True,
    )
    include = fields.Boolean(string="Inclure", default=False)
    transform_type = fields.Selection([
        ('none', 'Aucune'),
        ('strip', 'Supprimer espaces'),
        ('upper', 'Majuscules'),
        ('lower', 'Minuscules'),
        ('concat', 'Concaténer avec autre colonne'),
    ], default='strip', string="Transformation")
    concat_column = fields.Char(
        string="Colonne à concaténer",
        help="Nom de la colonne CSV à concaténer. Ex: 'Colonne2' ou plusieurs séparées par ; : 'Col1;Col2'"
    )
    concat_separator = fields.Char(
        string="Séparateur",
        default=" ",
        help="Séparateur entre les valeurs concaténées. Ex: ' ' ou ' - ' ou ' à compter de '"
    )

    @api.onchange('target_field_id')
    def _onchange_target_field_id(self):
        """Auto-cocher include quand un champ est sélectionné."""
        if self.target_field_id:
            self.include = True
