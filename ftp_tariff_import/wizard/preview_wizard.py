# -*- coding: utf-8 -*-
import csv
import html
import os
import re
from datetime import datetime, timezone

from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging
import json

from ..models.backend import sanitize_null_bytes

_logger = logging.getLogger(__name__)


class FtpPreviewWizard(models.TransientModel):
    _name = "ftp.preview.wizard"
    _description = "FTP/SFTP/IMAP Preview Wizard"

    provider_id = fields.Many2one("ftp.provider", string="Provider", required=True)
    search_filter = fields.Char(string="Filter (filename/path contains)")
    limit = fields.Integer(string="Max files", default=500)
    line_ids = fields.One2many("ftp.preview.wizard.line", "wizard_id", string="Files")
    preview_html = fields.Html(string="Preview", sanitize=False)
    script_text = fields.Text(string="Script (nettoyage/normalisation)")
    script_applied = fields.Boolean(string="Script appliqué", default=False, readonly=True)
    script_flags_json = fields.Text(string="Flags appliqués (JSON)", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        provider = None
        if self.env.context.get("default_provider_id"):
            provider = self.env["ftp.provider"].browse(self.env.context["default_provider_id"])
            res.setdefault("provider_id", provider.id)
            res.setdefault("limit", provider.max_preview or 500)

            # IMAP: do NOT preload list to keep UI responsive; user will click "Rafraîchir"
            proto = (provider.protocol or "").lower()
            if proto == "imap":
                msg = _("IMAP: click 'Refresh' to list attachments (criteria: %s, pattern: %s).") % (
                    (getattr(provider, "imap_search_criteria", None) or "ALL"),
                    (provider.file_pattern or "*"),
                )
                res["preview_html"] = "<p>%s</p>" % html.escape(msg)
            else:
                # Prefetch file list on open (better UX) for FTP/SFTP
                lines_vals = []
                try:
                    files = self.env["ftp.backend.service"].list_provider_files(
                        provider, preview_limit=(provider.max_preview or 500)
                    )
                    provider.sudo().write({
                        "last_connection_status": "ok",
                        "last_error": False,
                    })
                except Exception as e:
                    provider.sudo().write({
                        "last_connection_status": "failed",
                        "last_error": str(e),
                    })
                    # Display connection error context in preview area
                    err = _("Connection error: %s") % str(e)
                    res["preview_html"] = "<p>%s</p>" % html.escape(err)
                    # keep wizard open with error only; no files listed
                    files = []

                for f in files:
                    name = f.get("name") or ""
                    rpath = f.get("path") or name
                    ts = float(f.get("mtime") or 0.0)
                    dt = fields.Datetime.to_string(datetime.fromtimestamp(ts, timezone.utc)) if ts else False
                    lines_vals.append((0, 0, {
                        "checked": False,
                        "name": name,
                        "remote_path": rpath,
                        "size": int(f.get("size") or 0),
                        "mtime": dt,
                    }))
                if lines_vals:
                    res.setdefault("line_ids", [])
                    res["line_ids"] = [(5, 0, 0)] + lines_vals
                else:
                    # Provide a hint if no files were listed
                    msg = _("No files found in directory: %s with pattern: %s") % (provider.remote_dir_in or "/", provider.file_pattern or "*")
                    res["preview_html"] = "<p>%s</p>" % html.escape(msg)
        return res

    # Buttons / Actions
    def action_refresh(self):
        self.ensure_one()
        provider = self.provider_id.with_company(self.provider_id.company_id)
        service = self.env["ftp.backend.service"]
        lines_vals = []
        proto = (provider.protocol or "").lower()
        meta = {}
        try:
            if proto == "imap":
                files, meta = service.list_provider_files_with_meta(provider, preview_limit=(self.limit or provider.max_preview or 500))
            else:
                files = service.list_provider_files(provider, preview_limit=(self.limit or provider.max_preview or 500))
            # optional in-context test flag -> update provider last status
            provider.sudo().write({
                "last_connection_status": "ok",
                "last_error": False,
            })
        except Exception as e:
            provider.sudo().write({
                "last_connection_status": "failed",
                "last_error": str(e),
            })
            # Show the error in the preview instead of raising to keep the wizard usable
            self.write({
                "preview_html": "<p>%s</p>" % html.escape(_("Connection error: %s") % str(e)),
                "line_ids": [(5, 0, 0)],
            })
            return {
                "type": "ir.actions.act_window",
                "res_model": "ftp.preview.wizard",
                "res_id": self.id,
                "view_mode": "form",
                "target": "current",
            }

        flt = (self.search_filter or "").lower().strip()
        files_data = []
        for f in files:
            name = f.get("name") or ""
            rpath = f.get("path") or name
            if flt and flt not in name.lower() and flt not in rpath.lower():
                continue
            ts = float(f.get("mtime") or 0.0)
            dt = fields.Datetime.to_string(datetime.fromtimestamp(ts, timezone.utc)) if ts else False
            files_data.append({
                "name": name,
                "remote_path": rpath,
                "size": int(f.get("size") or 0),
                "mtime": dt,
                "mtime_ts": ts,  # Pour le tri
            })
        
        # Trier par date décroissante (le plus récent en premier)
        files_data.sort(key=lambda x: x.get("mtime_ts") or 0, reverse=True)
        
        # Créer les lignes avec le premier (plus récent) sélectionné par défaut
        for idx, fd in enumerate(files_data):
            lines_vals.append((0, 0, {
                "checked": (idx == 0),  # Sélectionner le plus récent par défaut
                "name": fd["name"],
                "remote_path": fd["remote_path"],
                "size": fd["size"],
                "mtime": fd["mtime"],
            }))
        
        # Reset preview and replace lines; if none, show an informative message
        if lines_vals:
            self.write({
                "preview_html": False,
                "line_ids": [(5, 0, 0)] + lines_vals,
            })
        else:
            proto = (provider.protocol or "").lower()
            if proto == "imap":
                # Normalize mailbox for display
                mbox = (provider.remote_dir_in or "INBOX").strip() or "INBOX"
                if mbox in ("/", "."):
                    mbox = "INBOX"
                while mbox.startswith("/"):
                    mbox = mbox[1:] or "INBOX"
                crit = (getattr(provider, "imap_search_criteria", None) or "ALL")
                patt = (provider.file_pattern or "*")
                limit_n = int(self.limit or provider.max_preview or 500)
                search_n = int((meta.get("search_count") or 0))
                scanned_n = int((meta.get("scanned_candidates") or 0))
                hint = _("No IMAP attachments found. Mailbox: %s, Criteria: %s, Pattern: %s, Limit: %s. SEARCH matched: %s; scanned newest: %s. Try Criteria=ALL and set Max files=1.") % (mbox, crit, patt, limit_n, search_n, scanned_n)
            else:
                hint = _("No files found in directory: %s with pattern: %s") % (provider.remote_dir_in or "/", provider.file_pattern or "*")
            self.write({
                "preview_html": "<p>%s</p>" % html.escape(hint),
                "line_ids": [(5, 0, 0)],
            })
        return {
            "type": "ir.actions.act_window",
            "res_model": "ftp.preview.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_select_all(self):
        for wiz in self:
            wiz.line_ids.write({"checked": True})
        return self._reopen_self()

    def action_apply_script(self):
        """Download the first selected file and render a cleaned preview using PIM script flags."""
        self.ensure_one()
        provider = self.provider_id.with_company(self.provider_id.company_id)
        paths = self._get_selected_paths()
        if not paths:
            # if none selected, take first line
            first = self.line_ids[:1]
            paths = [first.remote_path] if first else []
        if not paths:
            raise UserError(_("Select a file to preview."))

        remote_path = paths[0]
        backend = self.env["ftp.backend.service"]
        local_path = None
        try:
            local_path, size = backend.download_to_temp(provider, remote_path)
            # Use Planète PIM importer preview with flags parsed from script
            pim_importer = self.env["planete.pim.importer"]
            params = provider.get_csv_reader_params()
            flags = pim_importer._parse_script_flags(self.script_text or "")
            preview_html = pim_importer.build_preview_html(
                local_path,
                has_header=params.get("has_header", True),
                delimiter=params.get("delimiter"),
                encoding=params.get("encoding"),
                flags=flags,
                delimiter_regex=params.get("delimiter_regex"),
            )
            # Sanitize null bytes to prevent PostgreSQL errors
            self.write({
                "preview_html": sanitize_null_bytes(preview_html or ""),
                "script_applied": True,
                "script_flags_json": json.dumps(flags or {}),
            })
        finally:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass
        return self._reopen_self()

    def action_unselect_all(self):
        for wiz in self:
            wiz.line_ids.write({"checked": False})
        return self._reopen_self()

    def _get_selected_paths(self):
        self.ensure_one()
        return [l.remote_path for l in self.line_ids if l.checked and l.remote_path]

    def action_preview_selected(self):
        """Download the first selected file and render the first N rows as HTML table.
        
        AMÉLIORÉ: 
        - Détection automatique du délimiteur avec fallback si la config ne fonctionne pas.
        - Extraction automatique des fichiers ZIP pour prévisualiser le contenu.
        """
        self.ensure_one()
        provider = self.provider_id.with_company(self.provider_id.company_id)
        paths = self._get_selected_paths()
        if not paths:
            # if none selected, take first line
            first = self.line_ids[:1]
            paths = [first.remote_path] if first else []
        if not paths:
            raise UserError(_("Select a file to preview."))

        remote_path = paths[0]
        backend = self.env["ftp.backend.service"]
        local_path = None
        extracted_path = None  # Pour le fichier extrait du ZIP
        try:
            local_path, size = backend.download_to_temp(provider, remote_path)
            
            # =====================================================================
            # NOUVEAU: Extraction automatique des fichiers ZIP pour prévisualisation
            # Si le fichier téléchargé est un ZIP, extraire le premier CSV/TXT
            # =====================================================================
            import zipfile
            import tempfile as _tmp
            
            try:
                if zipfile.is_zipfile(local_path):
                    _logger.info("[PREVIEW] 📦 Detected ZIP archive, extracting for preview...")
                    
                    with zipfile.ZipFile(local_path, 'r') as zf:
                        # Lister les fichiers (exclure les dossiers)
                        names = [n for n in zf.namelist() if not n.endswith("/")]
                        lower_names = [n.lower() for n in names]
                        
                        # Vérifier si c'est un Excel (.xlsx = ZIP avec xl/)
                        if any(n.startswith("xl/") or "/xl/" in n for n in lower_names):
                            raise UserError(_(
                                "Le fichier '%s' est un Excel (.xlsx). "
                                "Veuillez l'exporter en CSV (séparateur ';' ou ',', encodage UTF-8/latin-1)."
                            ) % remote_path)
                        
                        # Chercher un CSV ou TXT
                        pick = None
                        for ext in (".csv", ".txt"):
                            for n in names:
                                if n.lower().endswith(ext):
                                    pick = n
                                    break
                            if pick:
                                break
                        
                        # Fallback: prendre le plus gros fichier
                        if not pick and names:
                            pick = max(names, key=lambda n: (zf.getinfo(n).file_size or 0))
                        
                        if pick:
                            # Extraire vers un fichier temporaire
                            fd, extracted_path = _tmp.mkstemp(prefix="pim_preview_zip_", suffix="_" + os.path.basename(pick))
                            os.close(fd)
                            
                            with open(extracted_path, "wb") as out:
                                out.write(zf.read(pick))
                            
                            _logger.info("[PREVIEW] ✅ Extracted '%s' from ZIP for preview (%.2f KB)", 
                                        pick, os.path.getsize(extracted_path) / 1024)
                            
                            # Utiliser le fichier extrait pour la prévisualisation
                            local_path = extracted_path
                            # Mettre à jour remote_path pour l'affichage
                            remote_path = f"{remote_path} → {pick}"
                        else:
                            _logger.warning("[PREVIEW] ⚠️ ZIP vide ou sans fichiers CSV/TXT!")
                            
            except zipfile.BadZipFile:
                # Pas un vrai ZIP, continuer avec le fichier original
                _logger.debug("[PREVIEW] File is not a valid ZIP, processing as-is")
            except UserError:
                raise
            except Exception as zip_err:
                _logger.warning("[PREVIEW] Error checking/extracting ZIP: %s", zip_err)
            # Parse first N rows
            params = provider.get_csv_reader_params()
            delimiter = params["delimiter"]
            has_header = params["has_header"]
            max_rows = int(params.get("max_preview_rows") or provider.max_preview_rows or 200)
            encoding = params.get("encoding") or "utf-8"
            delimiter_regex = params.get("delimiter_regex")

            rows = []
            enc_candidates = [encoding] if encoding else []
            for e in ("utf-8-sig", "cp1252", "latin-1"):
                if e not in enc_candidates:
                    enc_candidates.append(e)
            selected_enc = None
            for enc_try in enc_candidates:
                try:
                    with open(local_path, "r", encoding=enc_try, newline="") as tf:
                        tf.read(8192)
                    selected_enc = enc_try
                    break
                except UnicodeDecodeError:
                    continue
                except Exception:
                    selected_enc = enc_try
                    break
            if not selected_enc:
                selected_enc = encoding or "utf-8"
            
            # ================================================================
            # AMÉLIORATION: Détection automatique du délimiteur
            # Si le délimiteur configuré ne donne pas de bons résultats (<=1 colonne),
            # on essaye la détection automatique.
            # ================================================================
            detected_delimiter = None
            use_auto_detection = False
            
            # Lire un échantillon pour la détection
            with open(local_path, "r", encoding=selected_enc, errors="replace", newline="") as f:
                sample = f.read(8192)
            
            # Fonction de détection (similaire à celle de planete.pim.importer)
            def _detect_delimiter_smart(sample_text):
                if not sample_text:
                    return ","
                lines = sample_text.split('\n')[:10]
                if not lines:
                    return ","
                # Délimiteurs à tester, par ordre de préférence
                candidates = ['\t', ';', ',', '|']
                best_delimiter = ","
                best_score = 0
                import io
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
                        consistency = 1.0 - (max(col_counts) - min(col_counts)) / max(max(col_counts), 1)
                        bonus = 1.2 if delim == '\t' else 1.0
                        score = avg_cols * consistency * bonus
                        if score > best_score and avg_cols > 1:
                            best_score = score
                            best_delimiter = delim
                    except Exception:
                        continue
                return best_delimiter
            
            # Tester d'abord avec la config actuelle
            test_cols = 0
            if delimiter_regex:
                try:
                    pattern = re.compile(delimiter_regex)
                    first_line = sample.split('\n')[0] if sample else ""
                    test_cols = len(pattern.split(first_line.rstrip("\r\n"))) if first_line else 0
                except Exception:
                    test_cols = 0
            elif delimiter and len(delimiter) == 1:
                try:
                    import io
                    reader = csv.reader(io.StringIO(sample.split('\n')[0] if sample else ""), delimiter=delimiter, quotechar='"')
                    first_row = next(reader, [])
                    test_cols = len(first_row)
                except Exception:
                    test_cols = 0
            
            # Si la config donne 1 ou 0 colonnes, activer la détection automatique
            if test_cols <= 1:
                detected_delimiter = _detect_delimiter_smart(sample)
                use_auto_detection = True
                delimiter_regex = None  # Désactiver le regex si la détection auto est utilisée
                _logger.info("Preview: Config gave %d cols, switching to auto-detected delimiter: %r", test_cols, detected_delimiter)
            
            with open(local_path, "r", encoding=selected_enc, errors="replace", newline="") as f:
                header = None
                # Utiliser le délimiteur détecté si la détection auto est activée
                effective_delimiter = detected_delimiter if use_auto_detection else delimiter
                
                if delimiter_regex and not use_auto_detection:
                    pattern = re.compile(delimiter_regex)
                    if has_header:
                        first_line = f.readline()
                        header = [] if first_line == "" else pattern.split(first_line.rstrip("\r\n"))
                    if header:
                        rows.append(header)
                    i = 0
                    for line in f:
                        rows.append(pattern.split(line.rstrip("\r\n")))
                        i += 1
                        if i >= max_rows:
                            break
                elif effective_delimiter and len(effective_delimiter) == 1:
                    # Utiliser csv.reader avec quotechar pour gérer les guillemets
                    reader = csv.reader(f, delimiter=effective_delimiter, quotechar='"')
                    if has_header:
                        try:
                            header = next(reader)
                        except StopIteration:
                            header = []
                    if header:
                        rows.append(header)
                    for i, row in enumerate(reader, start=1):
                        rows.append(row)
                        if i >= max_rows:
                            break
                else:
                    # Multi-character delimiter (up to 5 chars): manual split
                    if has_header:
                        first_line = f.readline()
                        header = [] if first_line == "" else first_line.rstrip("\r\n").split(effective_delimiter or ",")
                    if header:
                        rows.append(header)
                    i = 0
                    for line in f:
                        rows.append(line.rstrip("\r\n").split(effective_delimiter or ","))
                        i += 1
                        if i >= max_rows:
                            break

            # Build simple HTML table - sanitize null bytes to prevent PostgreSQL errors
            def td(val):
                safe_val = sanitize_null_bytes("" if val is None else str(val))
                return "<td>%s</td>" % html.escape(safe_val)
            html_rows = []
            for ridx, row in enumerate(rows):
                tds = "".join(td(v) for v in row)
                tag = "th" if (has_header and ridx == 0) else "td"
                if tag == "th":
                    tds = "".join("<th>%s</th>" % html.escape(sanitize_null_bytes("" if v is None else str(v))) for v in row)
                html_rows.append("<tr>%s</tr>" % tds)
            table = "<h4>%s</h4><p>%s</p><table class='o_list_view table table-sm table-striped'><tbody>%s</tbody></table>" % (
                html.escape(sanitize_null_bytes(remote_path or "")),
                _("Showing up to %s rows.") % max_rows,
                "".join(html_rows),
            )
            self.write({"preview_html": sanitize_null_bytes(table)})
        finally:
            # Nettoyer les fichiers temporaires
            if extracted_path and os.path.exists(extracted_path):
                try:
                    os.remove(extracted_path)
                except Exception:
                    pass
            if local_path and local_path != extracted_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass
        return self._reopen_self()

    def action_download_selected(self):
        """Return an URL to stream selected file(s) to the browser (single or ZIP)."""
        self.ensure_one()
        if not self._get_selected_paths():
            raise UserError(_("Select at least one file to download."))
        return {
            "type": "ir.actions.act_url",
            "url": "/ftp_tariff_import/download/%d" % self.id,
            "target": "self",
        }

    def action_download_merged(self):
        """Download files and merge them into a single CSV (for multi-file providers like TD Synnex).
        
        Uses the merge_key configuration to JOIN MaterialFile + StockFile + TaxesGouv.
        """
        self.ensure_one()
        provider = self.provider_id.with_company(self.provider_id.company_id)
        
        # Vérifier que le mode multi-fichiers est activé
        if not getattr(provider, 'multi_file_mode', False):
            raise UserError(_("Le mode multi-fichiers n'est pas activé pour ce fournisseur. "
                            "Activez-le dans l'onglet 'Multi-fichiers' de la planification."))
        
        paths = self._get_selected_paths()
        if not paths:
            raise UserError(_("Sélectionnez au moins un fichier à fusionner."))
        
        # Stocker les fichiers dans le wizard pour le téléchargement via controller
        # Fusion sera faite par le controller
        return {
            "type": "ir.actions.act_url",
            "url": "/ftp_tariff_import/download_merged/%d" % self.id,
            "target": "self",
        }

    def action_preview_merged(self):
        """Preview the merged file content in the wizard (for multi-file providers)."""
        self.ensure_one()
        provider = self.provider_id.with_company(self.provider_id.company_id)
        
        # Vérifier que le mode multi-fichiers est activé
        if not getattr(provider, 'multi_file_mode', False):
            raise UserError(_("Le mode multi-fichiers n'est pas activé pour ce fournisseur. "
                            "Activez-le dans l'onglet 'Multi-fichiers' de la planification."))
        
        paths = self._get_selected_paths()
        if not paths:
            raise UserError(_("Sélectionnez au moins un fichier à fusionner."))
        
        # Import de la fusion depuis planete_pim
        from odoo.addons.planete_pim.models.multi_file_merger import merge_provider_files
        
        backend = self.env["ftp.backend.service"]
        local_files = []
        
        try:
            # 1. Télécharger tous les fichiers sélectionnés
            for remote_path in paths:
                local_path, size = backend.download_to_temp(provider, remote_path)
                local_files.append((remote_path, local_path))
            
            # 2. Identifier les fichiers selon les patterns
            material_pattern = provider.file_pattern_material or "MaterialFile*.txt"
            stock_pattern = provider.file_pattern_stock or "StockFile*.txt"
            taxes_pattern = provider.file_pattern_taxes or "TaxesGouv*.txt"
            
            import fnmatch
            
            material_content = None
            stock_content = None
            taxes_content = None
            
            for remote_path, local_path in local_files:
                filename = os.path.basename(remote_path)
                
                with open(local_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                
                if fnmatch.fnmatch(filename, material_pattern.replace('*', '*')):
                    material_content = content
                    _logger.info("[PREVIEW MERGED] Found Material file: %s", filename)
                elif fnmatch.fnmatch(filename, stock_pattern.replace('*', '*')):
                    stock_content = content
                    _logger.info("[PREVIEW MERGED] Found Stock file: %s", filename)
                elif fnmatch.fnmatch(filename, taxes_pattern.replace('*', '*')):
                    taxes_content = content
                    _logger.info("[PREVIEW MERGED] Found Taxes file: %s", filename)
            
            if not material_content:
                raise UserError(_("Fichier Material non trouvé! Pattern attendu: %s\n"
                                "Fichiers sélectionnés: %s") % (material_pattern, [p[0] for p in local_files]))
            
            # 3. Fusionner les fichiers
            tmp_path, headers = merge_provider_files(provider, material_content, stock_content, taxes_content)
            
            if not tmp_path:
                raise UserError(_("La fusion a échoué. Vérifiez que les fichiers ont le bon format."))
            
            # 4. Afficher un aperçu du fichier fusionné
            max_rows = int(provider.max_preview_rows or 200)
            rows = []
            
            with open(tmp_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.reader(f, delimiter=';')
                for i, row in enumerate(reader):
                    rows.append(row)
                    if i >= max_rows:
                        break
            
            # Build HTML table
            def td(val):
                safe_val = sanitize_null_bytes("" if val is None else str(val))
                return "<td>%s</td>" % html.escape(safe_val)
            
            html_rows = []
            for ridx, row in enumerate(rows):
                if ridx == 0:
                    # Header row
                    tds = "".join("<th>%s</th>" % html.escape(sanitize_null_bytes(v or "")) for v in row)
                else:
                    tds = "".join(td(v) for v in row)
                html_rows.append("<tr>%s</tr>" % tds)
            
            info_html = """
            <div class="alert alert-success">
                <strong>✅ Fusion réussie!</strong><br/>
                <b>Fichiers fusionnés:</b><br/>
                - Material: %s<br/>
                - Stock: %s<br/>
                - Taxes: %s<br/>
                <b>Lignes fusionnées:</b> %d<br/>
                <b>Colonnes:</b> %d
            </div>
            """ % (
                "✓" if material_content else "✗",
                "✓" if stock_content else "✗", 
                "✓" if taxes_content else "✗",
                len(rows) - 1,  # -1 for header
                len(headers),
            )
            
            table = "%s<h4>Aperçu du fichier fusionné</h4><p>%s</p><table class='o_list_view table table-sm table-striped'><tbody>%s</tbody></table>" % (
                info_html,
                _("Affichage des %s premières lignes.") % max_rows,
                "".join(html_rows),
            )
            
            self.write({"preview_html": sanitize_null_bytes(table)})
            
            # Cleanup temp merged file
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
                
        finally:
            # Cleanup downloaded files
            for remote_path, local_path in local_files:
                if local_path and os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass
        
        return self._reopen_self()

    def action_download_and_process_selected(self):
        """Download and process selected files within Odoo."""
        self.ensure_one()
        paths = self._get_selected_paths()
        if not paths:
            raise UserError(_("Select at least one file to import."))
        importer = self.env["ftp.tariff.importer"].with_company(self.provider_id.company_id)
        importer.process_selected_files(self.provider_id, paths)
        # Refresh provider last_run_at
        self.provider_id.sudo().write({"last_run_at": fields.Datetime.now()})
        return {"type": "ir.actions.act_window_close"}

    def action_open_mapping(self):
        self.ensure_one()
        paths = self._get_selected_paths()
        if not paths:
            raise UserError(_("Select at least one file to import."))
        # Mandatory Script gating: require that the script was applied and CLEAR_DUP enabled
        flags = {}
        try:
            if self.script_flags_json:
                flags = json.loads(self.script_flags_json)
        except Exception:
            flags = {}
        if not self.script_applied or not flags.get("ENABLE_CLEAR_DUP_BARCODES", False):
            raise UserError(_("Étape Script obligatoire: activez ENABLE_CLEAR_DUP_BARCODES=True et cliquez sur 'Appliquer le script' avant de continuer."))
        ctx = dict(
            self.env.context,
            default_provider_id=self.provider_id.id,
            selected_paths_json=json.dumps(paths),
            return_to_preview_id=self.id,
            pim_mode=True,
            pim_script_text=(self.script_text or ""),
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "ftp.mapping.wizard",
            "view_mode": "form",
            "target": "current",
            "context": ctx,
        }

    def action_back_to_provider(self):
        self.ensure_one()
        if self.provider_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "ftp.provider",
                "res_id": self.provider_id.id,
                "view_mode": "form",
                "target": "current",
            }
        return self._reopen_self()

    def _reopen_self(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "ftp.preview.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class FtpPreviewWizardLine(models.TransientModel):
    _name = "ftp.preview.wizard.line"
    _description = "FTP/SFTP/IMAP Preview Wizard Line"

    wizard_id = fields.Many2one("ftp.preview.wizard", required=True, ondelete="cascade")
    checked = fields.Boolean(string="Select", default=False)
    name = fields.Char(string="Filename")
    remote_path = fields.Char(string="Remote Path")
    size = fields.Integer(string="Size (bytes)")
    mtime = fields.Datetime(string="Modified (UTC)")
