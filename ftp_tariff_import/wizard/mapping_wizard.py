# -*- coding: utf-8 -*-
import csv
import html
import io
import json
import os
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)


class FtpMappingWizard(models.TransientModel):
    _name = "ftp.mapping.wizard"
    _description = "FTP/SFTP/IMAP Mapping Wizard"

    # Context
    provider_id = fields.Many2one("ftp.provider", string="Provider", required=True)
    selected_paths = fields.Text(string="Selected Remote Paths")
    step = fields.Selection(
        selection=[("select", "Sélectionner les colonnes"), ("map", "Configuration du mapping"), ("preview", "Aperçu")],
        default="select",
        required=True,
    )

    # Column list
    line_ids = fields.One2many("ftp.mapping.wizard.line", "wizard_id", string="Colonnes")

    # Mapping fields (dropdowns restricted to available columns in this wizard)
    product_name_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Nom du produit (Requis)", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )
    purchase_price_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Prix d'achat (Requis)", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )
    ean_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Code EAN", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )
    vendor_ref_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Référence fournisseur", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )
    stock_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Stock disponible", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )
    vat_rate_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Taux TVA (%)", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )
    category_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Catégorie", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )
    brand_col_id = fields.Many2one(
        "ftp.mapping.wizard.line", string="Marque", domain="[('wizard_id','=', id), ('selected','=', True)]"
    )

    # Extra mappings (user-defined target -> column)
    extra_line_ids = fields.One2many("ftp.mapping.wizard.extra", "wizard_id", string="Champs supplémentaires")

    # Template
    template_id = fields.Many2one("ftp.mapping.template", string="Template de mapping")
    template_name = fields.Char(string="Nom pour sauvegarde du template")
    return_to_preview_id = fields.Integer(string="ID Prévisualisation (retour)")

    # Preview
    preview_html = fields.Html(string="Aperçu", sanitize=False)
    mapping_html = fields.Html(string="Résumé du mapping", sanitize=False)

    # Helpers - read selected paths list
    def _get_paths(self):
        self.ensure_one()
        try:
            data = self.selected_paths or "[]"
            paths = json.loads(data)
            return [p for p in paths if p]
        except Exception:
            return []

    # Helpers - get available column names (from line_ids)
    def _available_columns(self):
        return [l.name for l in self.line_ids if l.name and l.selected]

    def _find_col_by_name(self, name):
        name = (name or "").strip()
        for line in self.line_ids:
            if line.selected and (line.name or "").strip() == name:
                return line
        return False

    def _enforce_selected_columns_on_mapping(self):
        """Unset mapping fields and extras that reference deselected columns."""
        sel_names = set(self._available_columns())

        def sanitize(field):
            rec = self[field]
            if rec and (rec.name or "").strip() not in sel_names:
                self[field] = False

        sanitize("product_name_col_id")
        sanitize("purchase_price_col_id")
        sanitize("ean_col_id")
        sanitize("vendor_ref_col_id")
        sanitize("stock_col_id")
        sanitize("vat_rate_col_id")
        sanitize("category_col_id")
        sanitize("brand_col_id")

        # Filter extras to selected columns only
        keep = []
        for ex in self.extra_line_ids:
            col = ex.column_id
            if col and col.selected and (col.name or "").strip() in sel_names:
                keep.append((0, 0, {"target_name": ex.target_name, "column_id": col.id}))
        if keep or self.extra_line_ids:
            self.extra_line_ids = [(5, 0, 0)] + keep

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        provider = None
        if self.env.context.get("default_provider_id"):
            provider = self.env["ftp.provider"].browse(self.env.context["default_provider_id"])
            res["provider_id"] = provider.id
        # selected paths passed in context as JSON
        paths = self.env.context.get("selected_paths_json")
        if paths:
            res["selected_paths"] = paths
        # carry return_to_preview_id if any (for back navigation)
        if self.env.context.get("return_to_preview_id"):
            try:
                res["return_to_preview_id"] = int(self.env.context.get("return_to_preview_id"))
            except Exception:
                pass
        # Probe first file headers to build columns list
        cols = []
        try:
            if provider and paths:
                try:
                    paths_list = json.loads(paths) if isinstance(paths, str) else paths
                except Exception:
                    paths_list = []
                first = paths_list[0] if paths_list else None
                if first:
                    # download temp and read headers with robust decoding
                    backend = self.env["ftp.backend.service"]
                    tmp_path, _ = backend.download_to_temp(provider, first)
                    try:
                        cols = self._read_headers(provider, tmp_path)
                    finally:
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
        except Exception as e:
            _logger.warning("Mapping wizard: cannot infer headers: %s", e)
        if cols:
            res["line_ids"] = [(0, 0, {"name": c, "selected": True}) for c in cols]
        return res

    def _decode_candidates(self, provider):
        # Provider-driven encoding preference
        enc = (provider.csv_encoding or "auto").strip()
        candidates = []
        if enc != "auto":
            candidates.append(enc)
        # common fallbacks
        for e in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            if e not in candidates:
                candidates.append(e)
        return candidates

    def _read_headers(self, provider, local_path, max_rows=4):
        # Return headers list using provider params; supports regex delimiter (e.g. r"\s{2,}")
        reader_params = {}
        try:
            reader_params = provider.get_csv_reader_params() or {}
        except Exception:
            reader_params = {}
        enc_pref = (reader_params.get("encoding") or provider.csv_encoding or "auto") or "auto"
        delimiter = reader_params.get("delimiter") or (provider.csv_delimiter or ";")
        delimiter_regex = reader_params.get("delimiter_regex")
        has_header = reader_params.get("has_header")
        if has_header is None:
            has_header = bool(getattr(provider, "csv_has_header", True))
        if delimiter == "\\t":
            delimiter = "\t"
        # Build encoding candidates (prefer explicit first)
        enc_candidates = []
        if enc_pref and enc_pref != "auto":
            enc_candidates.append(enc_pref)
        for e in self._decode_candidates(provider):
            if e not in enc_candidates:
                enc_candidates.append(e)
        for enc in enc_candidates:
            try:
                with open(local_path, "r", encoding=enc, newline="") as tf:
                    tf.read(4096)
                with open(local_path, "r", encoding=enc, errors="replace", newline="") as f:
                    headers = []
                    if delimiter_regex:
                        pattern = re.compile(delimiter_regex)
                        if has_header:
                            first_line = f.readline()
                            headers = pattern.split(first_line.rstrip("\r\n")) if first_line else []
                    elif delimiter and len(delimiter) == 1:
                        reader = csv.reader(f, delimiter=delimiter)
                        if has_header:
                            try:
                                headers = next(reader)
                            except StopIteration:
                                headers = []
                    else:
                        if has_header:
                            first_line = f.readline()
                            headers = [] if first_line == "" else first_line.rstrip("\r\n").split(delimiter)
                    return [h.strip() for h in headers]
            except UnicodeDecodeError:
                continue
            except Exception:
                continue
        # last resort
        try:
            with open(local_path, "r", encoding="latin-1", errors="replace", newline="") as f:
                headers = []
                if delimiter_regex:
                    pattern = re.compile(delimiter_regex)
                    if has_header:
                        first_line = f.readline()
                        headers = pattern.split(first_line.rstrip("\r\n")) if first_line else []
                elif delimiter and len(delimiter) == 1:
                    reader = csv.reader(f, delimiter=delimiter)
                    if has_header:
                        try:
                            headers = next(reader)
                        except StopIteration:
                            headers = []
                else:
                    if has_header:
                        first_line = f.readline()
                        headers = [] if first_line == "" else first_line.rstrip("\r\n").split(delimiter)
                return [h.strip() for h in headers]
        except Exception:
            return []

    # Buttons
    def action_select_all(self):
        for l in self.line_ids:
            l.selected = True
        return self._reopen_self()

    def action_unselect_all(self):
        for l in self.line_ids:
            l.selected = False
        return self._reopen_self()

    def action_to_map_step(self):
        self.ensure_one()
        self.step = "map"
        # Try auto-detect on first pass
        self._auto_detect_internal()
        # Drop mappings tied to deselected columns
        self._enforce_selected_columns_on_mapping()
        # Update mapping summary
        try:
            self.mapping_html = self._build_mapping_summary()
        except Exception:
            pass
        return self._reopen_self()

    def action_auto_detect(self):
        self.ensure_one()
        self._auto_detect_internal()
        return self._reopen_self()

    def action_back_to_select(self):
        self.ensure_one()
        self.step = "select"
        return self._reopen_self()

    def action_back_to_map(self):
        self.ensure_one()
        self.step = "map"
        return self._reopen_self()

    def action_back_to_preview_wizard(self):
        self.ensure_one()
        preview_id = self.return_to_preview_id or self.env.context.get("return_to_preview_id")
        if preview_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "ftp.preview.wizard",
                "res_id": int(preview_id),
                "view_mode": "form",
                "target": "current",
            }
        # Fallback: if no preview id, go back to step 1 within this wizard
        self.step = "select"
        return self._reopen_self()

    def _auto_detect_internal(self):
        cols = [c.lower() for c in self._available_columns()]

        def pick(*candidates):
            for c in candidates:
                for idx, col in enumerate(cols):
                    if c.lower() in col:
                        return self._available_columns()[idx]
            return False

        def set_id(field, name):
            rec = self._find_col_by_name(name)
            if rec:
                self[field] = rec.id

        # Heuristics
        if not self.product_name_col_id:
            set_id("product_name_col_id", pick("nom du produit", "product name", "designation", "libellé", "title", "name") or "")
        if not self.purchase_price_col_id:
            set_id("purchase_price_col_id", pick("prix d'achat", "purchase price", "cost", "standard price", "cost price", "buy") or "")
        if not self.ean_col_id:
            set_id("ean_col_id", pick("ean", "barcode", "ean13", "code barre") or "")
        if not self.vendor_ref_col_id:
            set_id("vendor_ref_col_id", pick("reference", "référence", "item no", "sku", "ref fournisseur") or "")
        if not self.stock_col_id:
            set_id("stock_col_id", pick("stock", "qty", "quantity", "available", "disponible") or "")
        if not self.vat_rate_col_id:
            set_id("vat_rate_col_id", pick("tva", "vat", "tax") or "")
        if not self.category_col_id:
            set_id("category_col_id", pick("categorie", "category", "famille", "rayon") or "")
        if not self.brand_col_id:
            set_id("brand_col_id", pick("marque", "brand", "manufacturer", "fabricant") or "")

    def action_preview_mapping(self):
        self.ensure_one()
        self.step = "preview"
        # Ensure mapping does not reference deselected columns
        self._enforce_selected_columns_on_mapping()
        # Build mapping summary before preview
        try:
            self.mapping_html = self._build_mapping_summary()
        except Exception:
            pass
        # Build preview using first file and first 4 rows
        paths = self._get_paths()
        if not paths:
            self.preview_html = _("<p>Aucun fichier sélectionné.</p>")
            return self._reopen_self()
        provider = self.provider_id.with_company(self.provider_id.company_id)
        backend = self.env["ftp.backend.service"]
        local_path = None
        try:
            local_path, _ = backend.download_to_temp(provider, paths[0])
            table = self._build_preview_table(provider, local_path, max_rows=4)
            self.preview_html = table
        finally:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass
        return self._reopen_self()

    def _build_preview_table(self, provider, local_path, max_rows=4):
        # read first rows and render a table with target columns
        targets = [
            ("Nom du produit", (self.product_name_col_id.name if self.product_name_col_id else None)),
            ("Prix d'achat", (self.purchase_price_col_id.name if self.purchase_price_col_id else None)),
            ("Code EAN", (self.ean_col_id.name if self.ean_col_id else None)),
            ("Référence fournisseur", (self.vendor_ref_col_id.name if self.vendor_ref_col_id else None)),
            ("Stock disponible", (self.stock_col_id.name if self.stock_col_id else None)),
            ("Taux TVA (%)", (self.vat_rate_col_id.name if self.vat_rate_col_id else None)),
            ("Catégorie", (self.category_col_id.name if self.category_col_id else None)),
            ("Marque", (self.brand_col_id.name if self.brand_col_id else None)),
        ]
        # Include extras
        for ex in self.extra_line_ids:
            targets.append((ex.target_name or "", ex.column_id.name if ex.column_id else None))

        # CSV/TXT read with provider params; supports regex delimiter (e.g. r"\s{2,}")
        rows = []
        headers = []
        header_map = {}
        reader_params = {}
        try:
            reader_params = provider.get_csv_reader_params() or {}
        except Exception:
            reader_params = {}
        enc_pref = (reader_params.get("encoding") or provider.csv_encoding or "auto") or "auto"
        delimiter = reader_params.get("delimiter") or (provider.csv_delimiter or ";")
        delimiter_regex = reader_params.get("delimiter_regex")
        has_header = reader_params.get("has_header")
        if has_header is None:
            has_header = bool(getattr(provider, "csv_has_header", True))
        if delimiter == "\\t":
            delimiter = "\t"
        # Build encoding candidates
        enc_candidates = []
        if enc_pref and enc_pref != "auto":
            enc_candidates.append(enc_pref)
        for e in self._decode_candidates(provider):
            if e not in enc_candidates:
                enc_candidates.append(e)
        for enc in enc_candidates:
            try:
                with open(local_path, "r", encoding=enc, errors="replace", newline="") as f:
                    if delimiter_regex:
                        pattern = re.compile(delimiter_regex)
                        if has_header:
                            first_line = f.readline()
                            headers = pattern.split(first_line.rstrip("\r\n")) if first_line else []
                        header_map = {h.strip(): idx for idx, h in enumerate(headers)}
                        i = 0
                        for line in f:
                            rows.append(pattern.split(line.rstrip("\r\n")))
                            i += 1
                            if i >= max_rows:
                                break
                    elif delimiter and len(delimiter) == 1:
                        reader = csv.reader(f, delimiter=delimiter)
                        if has_header:
                            try:
                                headers = next(reader)
                            except StopIteration:
                                headers = []
                        header_map = {h.strip(): idx for idx, h in enumerate(headers)}
                        for i, r in enumerate(reader, start=1):
                            rows.append(r)
                            if i >= max_rows:
                                break
                    else:
                        if has_header:
                            first_line = f.readline()
                            headers = [] if first_line == "" else first_line.rstrip("\r\n").split(delimiter)
                        header_map = {h.strip(): idx for idx, h in enumerate(headers)}
                        i = 0
                        for line in f:
                            rows.append(line.rstrip("\r\n").split(delimiter))
                            i += 1
                            if i >= max_rows:
                                break
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                continue
        # Build HTML
        def value_of(row, col_name):
            if not col_name or not headers:
                return ""
            idx = header_map.get(col_name)
            if idx is None or idx >= len(row):
                return ""
            return row[idx]
        head_html = "".join("<th>%s</th>" % html.escape(t[0]) for t in targets)
        body_html = ""
        for r in rows:
            tds = []
            for label, col in targets:
                tds.append("<td>%s</td>" % html.escape("" if col in (False, None, "") else str(value_of(r, col))))
            body_html += "<tr>%s</tr>" % "".join(tds)
        return "<table class='o_list_view table table-sm table-striped'><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head_html, body_html)

    def action_save_template(self):
        self.ensure_one()
        name = (self.template_name or "").strip() or _("Template %s") % fields.Datetime.now()
        mapping = self._collect_mapping_dict()
        selected = [l.name for l in self.line_ids if l.selected]
        self.env["ftp.mapping.template"].create_from_wizard(
            name=name,
            provider=self.provider_id,
            mapping_dict=mapping,
            selected_columns=selected,
        )
        # Refresh mapping summary and keep wizard open
        try:
            self.mapping_html = self._build_mapping_summary()
        except Exception:
            pass
        return self._reopen_self()

    def action_load_template(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Sélectionnez un template à charger."))
        mapping, selected = self.template_id.get_mapping()
        # Set selected columns
        self.line_ids.write({"selected": False})
        for l in self.line_ids:
            if l.name in (selected or []):
                l.selected = True
        # Load base mapping
        def set_from_name(field, name):
            rec = self._find_col_by_name(name)
            self[field] = rec.id if rec else False
        set_from_name("product_name_col_id", (mapping or {}).get("product_name"))
        set_from_name("purchase_price_col_id", (mapping or {}).get("purchase_price"))
        set_from_name("ean_col_id", (mapping or {}).get("ean"))
        set_from_name("vendor_ref_col_id", (mapping or {}).get("vendor_ref"))
        set_from_name("stock_col_id", (mapping or {}).get("stock"))
        set_from_name("vat_rate_col_id", (mapping or {}).get("vat_rate"))
        set_from_name("category_col_id", (mapping or {}).get("category"))
        set_from_name("brand_col_id", (mapping or {}).get("brand"))
        # Load extras
        self.extra_line_ids = [(5, 0, 0)]
        for ex in (mapping or {}).get("extra", []) or []:
            tgt = (ex.get("target") or "").strip()
            colname = (ex.get("column") or "").strip()
            rec = self._find_col_by_name(colname)
            self.extra_line_ids = [(0, 0, {"target_name": tgt, "column_id": rec.id if rec else False})] + self.extra_line_ids
        return self._reopen_self()

    def action_import(self):
        self.ensure_one()
        pim_mode = bool(self.env.context.get("pim_mode"))
        paths = self._get_paths()
        if not paths:
            raise UserError(_("Aucun fichier sélectionné."))
        mapping = self._collect_mapping_dict()
        selected = [l.name for l in self.line_ids if l.selected]
        if not selected:
            raise UserError(_("Aucune colonne sélectionnée."))
        if pim_mode:
            # PIM path: require product name and EAN only
            if not self.product_name_col_id or not self.ean_col_id:
                raise UserError(_("Veuillez renseigner 'Nom du produit' et 'Code EAN' (requis pour PIM)."))
            # Mandatory Script safeguard: require CLEAR_DUP flag enabled
            pim_script_text = self.env.context.get("pim_script_text") or ""
            flags = self.env["planete.pim.importer"]._parse_script_flags(pim_script_text)
            if not flags.get("ENABLE_CLEAR_DUP_BARCODES", False):
                raise UserError(_("Étape Script obligatoire: ENABLE_CLEAR_DUP_BARCODES=True doit être activé avant l'import PIM."))
            
            # ========================================
            # IMPORT ASYNCHRONE VIA JOB (évite timeout HTTP)
            # ========================================
            return self._create_pim_import_job(paths, mapping, selected, pim_script_text)
        # Default legacy mapping behaviour
        if not self.product_name_col_id or not self.purchase_price_col_id:
            raise UserError(_("Veuillez renseigner 'Nom du produit' et 'Prix d'achat' (requis)."))
        if not self.ean_col_id:
            raise UserError(_("Le champ 'Code EAN' doit être mappé pour identifier les produits."))
        importer = self.env["ftp.tariff.importer"].with_company(self.provider_id.company_id)
        try:
            result = importer.process_with_mapping(self.provider_id, paths, mapping, selected)
            totals = result or {}
            total = totals.get("total", 0) if isinstance(totals, dict) else 0
            ok = totals.get("success", 0) if isinstance(totals, dict) else 0
            err = totals.get("error", 0) if isinstance(totals, dict) else 0
            msg = _("Lignes: %d, Succès: %d, Erreurs: %d") % (total, ok, err)
            notif_type = "success" if err == 0 else "warning"
            title = _("Import terminé") if err == 0 else _("Import terminé avec erreurs")
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": title,
                    "message": msg,
                    "type": notif_type,
                    "sticky": False,
                },
            }
        except Exception as e:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Import échoué"),
                    "message": str(e),
                    "type": "danger",
                    "sticky": True,
                },
            }

    def _build_mapping_summary(self):
        # Compose an HTML summary of current mapping selection
        rows = []
        def add_row(label, col):
            rows.append("<tr><th style='text-align:left;padding-right:8px;'>{}</th><td>{}</td></tr>".format(
                html.escape(label or ""), html.escape(col or "")
            ))
        add_row("Nom du produit", self.product_name_col_id.name if self.product_name_col_id else "")
        add_row("Prix d'achat", self.purchase_price_col_id.name if self.purchase_price_col_id else "")
        add_row("Code EAN", self.ean_col_id.name if self.ean_col_id else "")
        add_row("Référence fournisseur", self.vendor_ref_col_id.name if self.vendor_ref_col_id else "")
        add_row("Stock disponible", self.stock_col_id.name if self.stock_col_id else "")
        add_row("Taux TVA (%)", self.vat_rate_col_id.name if self.vat_rate_col_id else "")
        add_row("Catégorie", self.category_col_id.name if self.category_col_id else "")
        add_row("Marque", self.brand_col_id.name if self.brand_col_id else "")
        # Extras
        if self.extra_line_ids:
            rows.append("<tr><th colspan='2' style='text-align:left;padding-top:8px;'>Champs supplémentaires</th></tr>")
            for ex in self.extra_line_ids:
                rows.append("<tr><td style='padding-left:12px;'>{}</td><td>{}</td></tr>".format(
                    html.escape(ex.target_name or ""), html.escape(ex.column_id.name or "")
                ))
        return "<table class='o_list_view table table-sm table-striped'>{}</table>".format("".join(rows))

    def _collect_mapping_dict(self):
        return {
            "product_name": (self.product_name_col_id.name if self.product_name_col_id else "").strip(),
            "purchase_price": (self.purchase_price_col_id.name if self.purchase_price_col_id else "").strip(),
            "ean": (self.ean_col_id.name if self.ean_col_id else "").strip(),
            "vendor_ref": (self.vendor_ref_col_id.name if self.vendor_ref_col_id else "").strip(),
            "stock": (self.stock_col_id.name if self.stock_col_id else "").strip(),
            "vat_rate": (self.vat_rate_col_id.name if self.vat_rate_col_id else "").strip(),
            "category": (self.category_col_id.name if self.category_col_id else "").strip(),
            "brand": (self.brand_col_id.name if self.brand_col_id else "").strip(),
            "extra": [
                {"target": (l.target_name or "").strip(), "column": (l.column_id.name or "").strip()}
                for l in self.extra_line_ids if l.target_name and l.column_id
            ],
        }

    def _create_pim_import_job(self, paths, mapping, selected_columns, script_text):
        """Crée un job d'import PIM asynchrone au lieu d'exécuter l'import directement.
        
        Avantages:
        - Évite le timeout HTTP (15 minutes max sur Odoo.sh)
        - L'import tourne en arrière-plan via le cron
        - Permet de voir la progression en temps réel
        - Permet la reprise en cas d'interruption (checkpoint)
        """
        import base64
        
        provider = self.provider_id.with_company(self.provider_id.company_id)
        backend = self.env["ftp.backend.service"]
        Job = self.env["planete.pim.import.job"]
        
        # Vérifier qu'un job n'est pas déjà en cours pour ce provider
        running_jobs = Job.search([
            ("provider_id", "=", provider.id),
            ("state", "in", ["pending", "running"]),
        ], limit=1)
        if running_jobs:
            raise UserError(_(
                "Un import est déjà en cours ou en attente pour ce provider.\n"
                "Attendez qu'il se termine ou annulez-le avant d'en lancer un nouveau.\n"
                "Job: %s (État: %s)"
            ) % (running_jobs.name, running_jobs.state))
        
        # Télécharger le fichier depuis FTP
        local_path = None
        file_data = None
        file_name = paths[0] if paths else "import.csv"
        
        try:
            local_path, _ = backend.download_to_temp(provider, paths[0])
            with open(local_path, "rb") as f:
                file_data = base64.b64encode(f.read())
        except Exception as e:
            raise UserError(_("Impossible de télécharger le fichier: %s") % str(e))
        finally:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass
        
        # Préparer les options du job
        pim_importer = self.env["planete.pim.importer"]
        options = {
            "has_header": True,
            "provider_id": provider.id,
            "supplier_id": pim_importer._get_supplier_for_provider(provider),
            "script_default": script_text or "",
            "do_write": True,
            "mapping": mapping,
            "selected_columns": selected_columns,
        }
        
        # Récupérer les paramètres CSV du provider
        try:
            reader_params = provider.get_csv_reader_params() or {}
            if reader_params.get("encoding"):
                options["encoding"] = reader_params["encoding"]
            if reader_params.get("delimiter"):
                options["delimiter"] = reader_params["delimiter"]
            if reader_params.get("delimiter_regex"):
                options["delimiter_regex"] = reader_params["delimiter_regex"]
            if "has_header" in reader_params:
                options["has_header"] = reader_params["has_header"]
        except Exception:
            pass
        
        # Créer le job
        job = Job.create({
            "name": "[PIM FULL] %s - %s" % (provider.name, os.path.basename(file_name)),
            "state": "pending",
            "import_mode": "full",  # Mode FULL = création de nouveaux produits
            "provider_id": provider.id,
            "company_id": provider.company_id.id,
            "file_data": file_data,
            "file_data_name": os.path.basename(file_name),
            "options_json": json.dumps(options),
            "progress_status": "En attente de traitement par le cron...",
        })
        
        _logger.info("[PIM] Created async import job %s for provider %s", job.id, provider.name)
        
        # Retourner une action vers le job pour voir la progression
        return {
            "type": "ir.actions.act_window",
            "res_model": "planete.pim.import.job",
            "res_id": job.id,
            "view_mode": "form",
            "target": "current",
            "context": {"form_view_initial_mode": "readonly"},
        }

    def _reopen_self(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "ftp.mapping.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class FtpMappingWizardLine(models.TransientModel):
    _name = "ftp.mapping.wizard.line"
    _description = "FTP/SFTP/IMAP Mapping Wizard Column"

    wizard_id = fields.Many2one("ftp.mapping.wizard", required=True, ondelete="cascade")
    selected = fields.Boolean(string="Importer", default=True)
    name = fields.Char(string="Nom de colonne")


class FtpMappingWizardExtra(models.TransientModel):
    _name = "ftp.mapping.wizard.extra"
    _description = "FTP/SFTP/IMAP Mapping Wizard Extra Field"

    wizard_id = fields.Many2one("ftp.mapping.wizard", required=True, ondelete="cascade")
    target_name = fields.Char(string="Nom du champ (target)", required=True)
    column_id = fields.Many2one(
        "ftp.mapping.wizard.line",
        string="Colonne source",
        domain="[('wizard_id','=', wizard_id), ('selected','=', True)]",
        required=True,
    )
