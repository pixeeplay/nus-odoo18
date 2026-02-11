# -*- coding: utf-8 -*-
import csv
import os
import base64
import html
from contextlib import contextmanager
import io
import fnmatch

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import datetime, timezone
import re

from .backend import sanitize_null_bytes

import logging

_logger = logging.getLogger(__name__)

# Import multi-file merger (TD Synnex etc.)
try:
    from odoo.addons.planete_pim.models.multi_file_merger import merge_provider_files, MultiFileMerger
except ImportError:
    merge_provider_files = None
    MultiFileMerger = None


class FtpTariffImporter(models.AbstractModel):
    _name = "ftp.tariff.importer"
    _description = "FTP/SFTP/IMAP Tariff Importer Service"

    # ---------------------------
    # Advisory lock helpers
    # ---------------------------
    @contextmanager
    def _provider_lock(self, provider_id):
        cr = self.env.cr
        cr.execute("SELECT pg_try_advisory_lock(%s)", [int(provider_id)])
        locked = bool(cr.fetchone()[0])
        try:
            if not locked:
                yield False
            else:
                yield True
        finally:
            if locked:
                cr.execute("SELECT pg_advisory_unlock(%s)", [int(provider_id)])

    # ---------------------------
    # Public entry points
    # ---------------------------
    @api.model
    def cron_process_providers(self):
        """Run daily at 07:00 (configured in data).
        
        OPTIMISÉ: Skip les providers sans host/identifiants configurés pour éviter
        des erreurs inutiles et réduire la charge sur le serveur.
        """
        providers = self.env["ftp.provider"].search([
            ("active", "=", True),
            ("auto_process", "=", True),
        ])
        for provider in providers:
            # Skip providers sans host configuré (évite les erreurs de connexion inutiles)
            if not provider.host:
                _logger.debug("Cron skip provider %s: no host configured", provider.name)
                continue
            
            # Skip IMAP sans identifiants (utiliser getattr pour éviter AttributeError)
            if provider.protocol == "imap" and not getattr(provider, 'imap_server', provider.host):
                _logger.debug("Cron skip provider %s: IMAP without server configured", provider.name)
                continue
            
            # Skip FTP/SFTP sans username (généralement requis)
            if provider.protocol in ("ftp", "sftp") and not provider.username:
                _logger.debug("Cron skip provider %s: no username configured", provider.name)
                continue
            
            # Multi-company context
            with self.env.cr.savepoint():
                try:
                    self.with_company(provider.company_id).process_provider(provider)
                except Exception as e:
                    # Log en WARNING au lieu d'ERROR pour réduire le bruit
                    _logger.warning("Cron process failed for provider %s: %s", provider.name, e)
                    provider.sudo().write({
                        "last_connection_status": "failed",
                        "last_error": str(e),
                    })

    @api.model
    def process_provider(self, provider):
        """Process all files matching the provider pattern and update provider status."""
        provider = provider.with_company(provider.company_id)
        now = fields.Datetime.now()
        
        # Check if multi-file mode (TD Synnex etc.)
        if getattr(provider, 'multi_file_mode', False) and merge_provider_files:
            return self._process_multi_file_provider(provider)
        
        with self._provider_lock(provider.id) as ok:
            if not ok:
                _logger.info("Skip provider %s - lock busy", provider.display_name)
                return False
            try:
                # Mark provider as running at the start of processing
                provider.sudo().write({
                    "last_connection_status": "running",
                    "last_error": False,
                    "last_run_at": now,
                })
                backend = self.env["ftp.backend.service"]
                # List files (connection attempt)
                files = backend.list_provider_files(provider)
                
                # Trier par date décroissante (le plus récent en premier)
                files.sort(key=lambda f: float(f.get("mtime") or 0), reverse=True)
                
                if provider.max_files_per_run:
                    files = files[: int(provider.max_files_per_run)]

                total_logs = []
                # If no files, still create a log to reflect the successful connection/listing
                if not files:
                    Log = self.env["ftp.tariff.import.log"].with_company(provider.company_id)
                    log = Log.create({
                        "name": _("Tariff Import"),
                        "provider_id": provider.id,
                        "company_id": provider.company_id.id,
                        "protocol": (provider.protocol or "").lower(),
                        "file_name": _("No files"),
                    })
                    log.mark_started()
                    log.mark_done(total=0, success=0, error=0, msg=_("No files found on remote"))
                    total_logs.append(log.id)
                else:
                    for f in files:
                        with self.env.cr.savepoint():
                            log = self._process_single_file(provider, f["path"])
                            total_logs.append(log.id)

                # Mark success (even if no files were found, connection/listing succeeded)
                provider.sudo().write({
                    "last_connection_status": "ok",
                    "last_error": False,
                    "last_run_at": now,
                })
                return True
            except Exception as e:
                # Mark failure on any error
                provider.sudo().write({
                    "last_connection_status": "failed",
                    "last_error": str(e),
                    "last_run_at": now,
                })
                _logger.exception("Provider %s processing failed", provider.display_name)
                # Re-raise so callers (cron/wizard) can react if needed
                raise

    @api.model
    def _process_multi_file_provider(self, provider):
        """Process multi-file provider (TD Synnex style: MaterialFile + StockFile + TaxesGouv).
        
        Downloads multiple files, merges them on a common key (Matnr), then processes
        the merged result through the standard import pipeline.
        """
        provider = provider.with_company(provider.company_id)
        now = fields.Datetime.now()
        
        with self._provider_lock(provider.id) as ok:
            if not ok:
                _logger.info("Skip provider %s - lock busy (multi-file)", provider.display_name)
                return False
            
            try:
                # Mark provider as running
                provider.sudo().write({
                    "last_connection_status": "running",
                    "last_error": False,
                    "last_run_at": now,
                })
                
                backend = self.env["ftp.backend.service"]
                Log = self.env["ftp.tariff.import.log"].with_company(provider.company_id)
                
                # List all files on remote
                all_files = backend.list_provider_files(provider)
                
                # Get patterns
                pattern_material = provider.file_pattern_material or "MaterialFile*.txt"
                pattern_stock = provider.file_pattern_stock or "StockFile*.txt"
                pattern_taxes = provider.file_pattern_taxes or "TaxesGouv*.txt"
                
                # Find matching files
                def find_latest_match(files, pattern):
                    """Find the most recent file matching pattern."""
                    matches = []
                    for f in files:
                        fname = os.path.basename(f.get("path", ""))
                        if fnmatch.fnmatch(fname, pattern):
                            matches.append(f)
                    if not matches:
                        return None
                    # Sort by mtime descending, take first
                    matches.sort(key=lambda x: float(x.get("mtime") or 0), reverse=True)
                    return matches[0]
                
                material_file = find_latest_match(all_files, pattern_material)
                stock_file = find_latest_match(all_files, pattern_stock)
                taxes_file = find_latest_match(all_files, pattern_taxes)
                
                if not material_file:
                    _logger.warning("[MULTI-FILE] No Material file found matching pattern '%s'", pattern_material)
                    log = Log.create({
                        "name": _("Multi-File Import"),
                        "provider_id": provider.id,
                        "company_id": provider.company_id.id,
                        "protocol": (provider.protocol or "").lower(),
                        "file_name": _("No Material file found"),
                    })
                    log.mark_started()
                    log.mark_error(msg=_("No Material file found matching pattern: %s") % pattern_material)
                    provider.sudo().write({
                        "last_connection_status": "failed",
                        "last_error": _("No Material file found"),
                        "last_run_at": now,
                    })
                    return False
                
                # Create log entry
                file_names = [os.path.basename(material_file["path"])]
                if stock_file:
                    file_names.append(os.path.basename(stock_file["path"]))
                if taxes_file:
                    file_names.append(os.path.basename(taxes_file["path"]))
                
                log = Log.create({
                    "name": _("Multi-File Import (Merge)"),
                    "provider_id": provider.id,
                    "company_id": provider.company_id.id,
                    "protocol": (provider.protocol or "").lower(),
                    "file_name": " + ".join(file_names),
                })
                log.mark_started()
                
                # Download files
                local_paths = {}
                try:
                    _logger.info("[MULTI-FILE] Downloading Material: %s", material_file["path"])
                    local_paths["material"], _ = backend.download_to_temp(provider, material_file["path"])
                    
                    if stock_file:
                        _logger.info("[MULTI-FILE] Downloading Stock: %s", stock_file["path"])
                        local_paths["stock"], _ = backend.download_to_temp(provider, stock_file["path"])
                    
                    if taxes_file:
                        _logger.info("[MULTI-FILE] Downloading Taxes: %s", taxes_file["path"])
                        local_paths["taxes"], _ = backend.download_to_temp(provider, taxes_file["path"])
                    
                    # Read file contents
                    def read_content(path, encoding="utf-8"):
                        if not path:
                            return None
                        for enc in (encoding, "utf-8-sig", "cp1252", "latin-1"):
                            try:
                                with open(path, "r", encoding=enc, errors="replace") as f:
                                    return f.read()
                            except Exception:
                                continue
                        return None
                    
                    material_content = read_content(local_paths.get("material"))
                    stock_content = read_content(local_paths.get("stock"))
                    taxes_content = read_content(local_paths.get("taxes"))
                    
                    if not material_content:
                        raise UserError(_("Cannot read Material file content"))
                    
                    # Merge files
                    _logger.info("[MULTI-FILE] Merging files on key: %s", provider.multi_file_merge_key or "Matnr")
                    merged_path, merged_headers = merge_provider_files(
                        provider, material_content, stock_content, taxes_content
                    )
                    
                    if not merged_path:
                        raise UserError(_("Merge failed - no data produced"))
                    
                    # Log merge info
                    log_html = "<h5>Multi-File Merge</h5><ul>"
                    log_html += "<li>Material: %s</li>" % html.escape(os.path.basename(material_file["path"]))
                    if stock_file:
                        log_html += "<li>Stock: %s</li>" % html.escape(os.path.basename(stock_file["path"]))
                    if taxes_file:
                        log_html += "<li>Taxes: %s</li>" % html.escape(os.path.basename(taxes_file["path"]))
                    log_html += "</ul>"
                    log_html += "<p>Merge key: <code>%s</code></p>" % html.escape(provider.multi_file_merge_key or "Matnr")
                    log_html += "<p>Merged columns: %d</p>" % len(merged_headers)
                    log.write({"log_html": log_html})
                    
                    # Attach merged file to log
                    try:
                        with open(merged_path, "rb") as bf:
                            b64 = base64.b64encode(bf.read())
                        log.write({"file_data": b64, "file_data_name": "merged_data.csv"})
                    except Exception:
                        pass
                    
                    # Process merged file through standard pipeline
                    # Override delimiter to ; (our merged CSV uses ;)
                    original_delimiter = provider.csv_delimiter
                    provider.with_context(skip_schedule_sync=True).write({"csv_delimiter": ";"})
                    
                    try:
                        total, ok_count, error_count = self._import_csv_file(provider, merged_path, log)
                        log.mark_done(total=total, success=ok_count, error=error_count, msg=_("Multi-file merge import completed"))
                    finally:
                        # Restore original delimiter
                        provider.with_context(skip_schedule_sync=True).write({"csv_delimiter": original_delimiter})
                    
                    provider.sudo().write({
                        "last_connection_status": "ok",
                        "last_error": False,
                        "last_run_at": now,
                    })
                    return True
                    
                finally:
                    # Cleanup temp files
                    for path in local_paths.values():
                        if path and os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                    # Cleanup merged file
                    if 'merged_path' in dir() and merged_path and os.path.exists(merged_path):
                        try:
                            os.remove(merged_path)
                        except Exception:
                            pass
                            
            except Exception as e:
                provider.sudo().write({
                    "last_connection_status": "failed",
                    "last_error": str(e),
                    "last_run_at": now,
                })
                _logger.exception("[MULTI-FILE] Provider %s processing failed", provider.display_name)
                raise

    @api.model
    def process_selected_files(self, provider, remote_paths):
        """Process only selected remote paths (from preview wizard)."""
        provider = provider.with_company(provider.company_id)
        with self._provider_lock(provider.id) as ok:
            if not ok:
                raise UserError(_("A job is already running for this provider. Try again later."))
            for p in remote_paths:
                with self.env.cr.savepoint():
                    self._process_single_file(provider, p, archive=False)
        return True

    @api.model
    def process_with_mapping(self, provider, remote_paths, mapping, selected_columns):
        """Process selected files using a user-provided mapping (from mapping wizard)."""
        provider = provider.with_company(provider.company_id)
        mapping = mapping or {}
        selected_columns = set(selected_columns or [])
        with self._provider_lock(provider.id) as ok:
            if not ok:
                raise UserError(_("A job is already running for this provider. Try again later."))
            total_total = 0
            total_success = 0
            total_error = 0
            created_log_ids = []
            for remote_path in remote_paths or []:
                with self.env.cr.savepoint():
                    Log = self.env["ftp.tariff.import.log"].with_company(provider.company_id)
                    backend = self.env["ftp.backend.service"]
                    log = Log.create({
                        "name": _("Tariff Import (Mapping)"),
                        "provider_id": provider.id,
                        "company_id": provider.company_id.id,
                        "protocol": (provider.protocol or "").lower(),
                        "file_name": remote_path,
                        "is_mapping": True,
                    })
                    log.mark_started()
                    # Snapshot provider info and remote file mtime (best-effort)
                    try:
                        files_info = backend.list_provider_files(provider)
                        info = next((f for f in files_info if f.get("path") == remote_path), None)
                        if info and info.get("mtime"):
                            ts = float(info.get("mtime"))
                            dt = fields.Datetime.to_string(datetime.fromtimestamp(ts, timezone.utc))
                            log.write({"remote_mtime": dt})
                    except Exception:
                        pass
                    try:
                        log.write({
                            "provider_last_connection_status": provider.last_connection_status or False,
                            "provider_last_run_at": provider.last_run_at or False,
                        })
                    except Exception:
                        pass
                    local_path = None
                    try:
                        local_path, size = backend.download_to_temp(provider, remote_path)
                        # Attach original file
                        try:
                            import base64, os as _os
                            with open(local_path, "rb") as bf:
                                b64 = base64.b64encode(bf.read())
                            log.write({"file_data": b64, "file_data_name": _os.path.basename(remote_path)})
                        except Exception:
                            pass
                        # Append a small preview of the file to the log
                        try:
                            params = provider.get_csv_reader_params()
                            delimiter = params["delimiter"]
                            has_header = params["has_header"]
                            enc = params.get("encoding") or "utf-8"
                            enc_candidates = [enc]
                            for e in ("utf-8-sig", "cp1252", "latin-1"):
                                if e not in enc_candidates:
                                    enc_candidates.append(e)
                            selected_enc = None
                            for enc_try in enc_candidates:
                                try:
                                    with open(local_path, "r", encoding=enc_try, newline="") as tf:
                                        tf.read(4096)
                                    selected_enc = enc_try
                                    break
                                except Exception:
                                    continue
                            if not selected_enc:
                                selected_enc = enc
                            rows = []
                            headers = []
                            with open(local_path, "r", encoding=selected_enc, errors="replace", newline="") as f:
                                if delimiter and len(delimiter) == 1:
                                    r = csv.reader(f, delimiter=delimiter)
                                    if has_header:
                                        try:
                                            headers = next(r) or []
                                        except StopIteration:
                                            headers = []
                                    rows.append(headers)
                                    for i, row in enumerate(r, start=1):
                                        rows.append(row)
                                        if i >= 10:
                                            break
                                else:
                                    # Multi-character delimiter (up to 5 chars): manual split
                                    if has_header:
                                        first_line = f.readline()
                                        headers = [] if first_line == "" else first_line.rstrip("\r\n").split(delimiter)
                                    rows.append(headers)
                                    i = 0
                                    for line in f:
                                        rows.append(line.rstrip("\r\n").split(delimiter))
                                        i += 1
                                        if i >= 10:
                                            break
                            def esc(s):
                                try:
                                    return html.escape("" if s is None else str(s))
                                except Exception:
                                    return str(s)
                            body = ""
                            for idx, row in enumerate(rows):
                                cells = "".join("<th>%s</th>" % esc(v) if (has_header and idx == 0) else "<td>%s</td>" % esc(v) for v in row)
                                body += "<tr>%s</tr>" % cells
                            preview_html = "<h5>%s</h5><table class='o_list_view table table-sm table-striped'><tbody>%s</tbody></table>" % (html.escape(os.path.basename(remote_path) or ""), body)
                            log.write({"log_html": (log.log_html or "") + preview_html})
                        except Exception:
                            pass
                        total, ok_c, err_c = self._import_csv_file_with_mapping(provider, local_path, log, mapping, selected_columns)
                        # Manual mapping import: do NOT archive/move
                        log.mark_done(total=total, success=ok_c, error=err_c, msg=_("Imported with mapping"))
                        try:
                            log.write({"log_html": (log.log_html or "") + (_("<p>No remote move performed; file left in place.</p>"))})
                        except Exception:
                            pass
                        total_total += total
                        total_success += ok_c
                        total_error += err_c
                        created_log_ids.append(log.id)
                    except Exception as e:
                        log.mark_error(msg=str(e))
                        _logger.exception("Tariff import with mapping failed for %s", remote_path)
                    finally:
                        if local_path and os.path.exists(local_path):
                            try:
                                os.remove(local_path)
                            except Exception:
                                pass
        return {"total": total_total, "success": total_success, "error": total_error, "log_ids": created_log_ids}

    def process_with_mapping_pim(self, provider, remote_paths, mapping, selected_columns, script_text=""):
        """Build a minimal, cleaned dataset from the mapped columns and delegate creation to Planète PIM importer.
        Rules:
          - Only use mapped Name + EAN for product creation.
          - Optional supplier resolution: partner_id (if provided in file/extras) has priority over supplier_name.
          - Optional supplier price from mapped purchase_price, exported as 'product_cost' in EUR.
          - Script flags are forwarded to PIM engine; it will handle normalization/dedup/validation.
        Returns the last action produced by the PIM importer (opens the created log).
        """
        provider = provider.with_company(provider.company_id)
        mapping = mapping or {}
        selected_columns = set(selected_columns or [])
        last_action = None

        with self._provider_lock(provider.id) as ok:
            if not ok:
                raise UserError(_("A job is already running for this provider. Try again later."))
            backend = self.env["ftp.backend.service"]
            pim_importer = self.env["planete.pim.importer"].with_company(provider.company_id)

            for remote_path in remote_paths or []:
                local_path = None
                try:
                    local_path, _sz = backend.download_to_temp(provider, remote_path)
                    # Determine encoding/delimiter similarly to other flows
                    params = provider.get_csv_reader_params()
                    delimiter = params.get("delimiter") or ";"
                    has_header = bool(params.get("has_header"))
                    encoding = params.get("encoding") or "utf-8"
                    enc_candidates = [encoding]
                    for e in ("utf-8-sig", "cp1252", "latin-1"):
                        if e not in enc_candidates:
                            enc_candidates.append(e)
                    selected_enc = None
                    for enc_try in enc_candidates:
                        try:
                            with open(local_path, "r", encoding=enc_try, newline="") as tf:
                                tf.read(4096)
                            selected_enc = enc_try
                            break
                        except Exception:
                            continue
                    if not selected_enc:
                        selected_enc = encoding

                    # Prepare header mapping
                    with open(local_path, "r", encoding=selected_enc, errors="replace", newline="") as f:
                        headers = []
                        if delimiter and len(delimiter) == 1:
                            reader = csv.reader(f, delimiter=delimiter)
                            if has_header:
                                try:
                                    headers = next(reader) or []
                                except StopIteration:
                                    headers = []
                            data_iter = reader
                        else:
                            if has_header:
                                first_line = f.readline()
                                headers = [] if first_line == "" else first_line.rstrip("\r\n").split(delimiter)
                            # iterator over remaining lines
                            def _iter_lines(_f):
                                for _line in _f:
                                    yield _line.rstrip("\r\n").split(delimiter)
                            data_iter = _iter_lines(f)
                        headers = [h.strip() for h in headers]
                        hdr_index = {h: idx for idx, h in enumerate(headers)}

                        def get_cell(row, col_name):
                            if not col_name:
                                return ""
                            idx = hdr_index.get(col_name.strip())
                            if idx is None or idx >= len(row):
                                return ""
                            return (row[idx] or "").strip()

                        # Extract mapped column names
                        name_col = (mapping.get("product_name") or "").strip()
                        ean_col = (mapping.get("ean") or "").strip()
                        cost_col = (mapping.get("purchase_price") or "").strip()  # mapped purchase price -> product_cost
                        vendor_ref_col = (mapping.get("vendor_ref") or "").strip()

                        # Optional extras for supplier fields
                        partner_id_col = ""
                        supplier_name_col = ""
                        for ex in (mapping.get("extra") or []):
                            tgt = (ex.get("target") or "").strip().lower().replace(" ", "_")
                            col = (ex.get("column") or "").strip()
                            if tgt == "partner_id":
                                partner_id_col = col
                            elif tgt == "supplier_name":
                                supplier_name_col = col
                        # If extras not provided, try natural headers
                        if not partner_id_col:
                            for cand in ("partner_id", "supplier_id", "vendor_id"):
                                if cand in hdr_index:
                                    partner_id_col = cand
                                    break
                        if not supplier_name_col:
                            for cand in ("supplier_name", "fournisseur", "vendor", "fournisseur_nom"):
                                if cand in hdr_index:
                                    supplier_name_col = cand
                                    break

                        # Build minimal CSV for PIM importer
                        out = io.StringIO()
                        w = csv.writer(out, delimiter=",", lineterminator="\n")
                        out_headers = ["name", "barcode", "product_cost", "partner_id", "supplier_name"]
                        w.writerow(out_headers)

                        for row in data_iter:
                            name_val = get_cell(row, name_col) or ""
                            ean_val = get_cell(row, ean_col) or ""
                            cost_val = get_cell(row, cost_col) or ""
                            pid_val = get_cell(row, partner_id_col) if partner_id_col else ""
                            sname_val = get_cell(row, supplier_name_col) if supplier_name_col else ""
                            # Sanitize null bytes to prevent PostgreSQL errors
                            # Write values; PIM importer will normalize EAN, parse floats, and apply flags.
                            w.writerow([
                                sanitize_null_bytes(name_val),
                                sanitize_null_bytes(ean_val),
                                sanitize_null_bytes(cost_val),
                                sanitize_null_bytes(pid_val),
                                sanitize_null_bytes(sname_val),
                            ])

                        data_b64 = base64.b64encode(out.getvalue().encode("utf-8"))
                        # Delegate to PIM importer with do_write=True and script forwarded
                        options = {
                            "has_header": True,
                            "encoding": "utf-8",
                            "delimiter": ",",
                            "provider_id": provider.id,
                            "supplier_id": pim_importer._get_supplier_for_provider(provider),
                            "script_default": script_text or "",
                            "do_write": True,
                        }
                        # Use a filename that indicates it's a mapped feed
                        fname = "%s.pim_mapped.csv" % (os.path.basename(remote_path) or "feed")
                        last_action = pim_importer.import_from_binary(data_b64, fname, options=options)
                finally:
                    if local_path and os.path.exists(local_path):
                        try:
                            os.remove(local_path)
                        except Exception:
                            pass
        return last_action

    def _import_csv_file_with_mapping(self, provider, local_path, log, mapping, selected_columns):
        """Import using explicit mapping to product fields.
        Required mapping keys:
          - product_name (required)
          - purchase_price (required)
          - ean (required for identification)
        Optional:
          - vendor_ref, stock, vat_rate, category, brand
        """
        params = provider.get_csv_reader_params()
        delimiter = params["delimiter"]
        has_header = params["has_header"]
        dec_sep = params["decimal_separator"] or "."
        encoding = params["encoding"] or "utf-8"

        def parse_float_val(raw):
            if raw is None:
                return None
            s = str(raw).strip()
            if s == "":
                return None
            if dec_sep != ".":
                s = s.replace(dec_sep, ".")
            s = s.replace(" ", "")
            try:
                return float(s)
            except Exception:
                # try simple comma normalization
                try:
                    return float(s.replace(",", "."))
                except Exception:
                    return None

        total = 0
        ok_count = 0
        error_count = 0

        enc_candidates = [encoding] if encoding else []
        for e in ("utf-8-sig", "cp1252", "latin-1"):
            if e not in enc_candidates:
                enc_candidates.append(e)
        # ✅ FIX ENCODAGE: Lire le fichier ENTIER en binaire pour tester chaque encoding
        # (avant: seulement 8192 octets → les accents après cette position étaient corrompus)
        selected_enc = None
        try:
            with open(local_path, "rb") as bf:
                raw_bytes = bf.read()
        except Exception:
            raw_bytes = b""
        for enc_try in enc_candidates:
            try:
                raw_bytes.decode(enc_try)
                selected_enc = enc_try
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if not selected_enc:
            selected_enc = encoding or "utf-8"
        if selected_enc != encoding:
            _logger.info("[MAPPING] Encoding auto-detected: %s (configured: %s) for %s",
                        selected_enc, encoding, local_path)
            try:
                log.write({"log_html": (log.log_html or "") + (_("<p>Notice: encodage détecté: <b>%s</b> (configuré: %s)</p>") % (selected_enc, encoding))})
            except Exception:
                pass

        # Read and process rows
        # Try to auto-detect delimiter if configured one seems wrong (only for single-character delimiters)
        if delimiter and len(delimiter) == 1:
            try:
                import csv as _csv
                with open(local_path, "r", encoding=selected_enc, errors="replace", newline="") as _sf:
                    sample = _sf.read(4096)
                sniffer = _csv.Sniffer()
                sniffed = sniffer.sniff(sample, delimiters=[",",";","|","\t"])
                if sniffed and sniffed.delimiter and sniffed.delimiter != delimiter:
                    delimiter = sniffed.delimiter
                    try:
                        log.write({"log_html": (log.log_html or "") + (_("<p>Notice: delimiter auto-detected: %s</p>") % delimiter)})
                    except Exception:
                        pass
            except Exception:
                pass
        with open(local_path, "r", encoding=selected_enc, errors="replace", newline="") as f:
            headers = []
            if delimiter and len(delimiter) == 1:
                reader = csv.reader(f, delimiter=delimiter)
                if has_header:
                    try:
                        headers = next(reader)
                    except StopIteration:
                        headers = []
                data_iter = reader
            else:
                if has_header:
                    first_line = f.readline()
                    headers = [] if first_line == "" else first_line.rstrip("\r\n").split(delimiter)
                # iterator over remaining lines
                def _iter_lines(_f):
                    for _line in _f:
                        yield _line.rstrip("\r\n").split(delimiter)
                data_iter = _iter_lines(f)
            headers = [h.strip() for h in headers]
            hdr_index = {h: idx for idx, h in enumerate(headers)}
            # Mapping diagnostics vs headers
            try:
                def _present(colname):
                    cn = (colname or "").strip()
                    return "OK" if cn and cn in hdr_index else "Not found"
                base_map = {
                    "product_name": (mapping.get("product_name") or "").strip(),
                    "purchase_price": (mapping.get("purchase_price") or "").strip(),
                    "ean": (mapping.get("ean") or "").strip(),
                    "vendor_ref": (mapping.get("vendor_ref") or "").strip(),
                    "stock": (mapping.get("stock") or "").strip(),
                    "vat_rate": (mapping.get("vat_rate") or "").strip(),
                    "category": (mapping.get("category") or "").strip(),
                    "brand": (mapping.get("brand") or "").strip(),
                }
                rows_diag = "".join("<li>%s</li>" % html.escape("%s → %s" % (k + ": " + (v or ""), _present(v))) for k, v in base_map.items())
                extras = (mapping.get("extra") or [])
                if extras:
                    rows_diag += "<li>Extras:</li><ul>" + "".join("<li>%s</li>" % html.escape("%s → %s" % (((ex.get("target") or "") + " from " + (ex.get("column") or "")), _present(ex.get("column")))) for ex in extras) + "</ul>"
                diag_html = "<h5>Mapping vs headers</h5><ul>%s</ul>" % rows_diag
                log.write({"log_html": (log.log_html or "") + diag_html})
            except Exception:
                pass

            # Rules context for mapping flow
            try:
                _fname = os.path.basename(log.file_name or "") or os.path.basename(local_path or "")
            except Exception:
                _fname = os.path.basename(local_path or "")
            _base = _fname.rsplit(".", 1)[0]
            ref_clean = re.sub(r"[^A-Za-z0-9]", "", _base)
            date_du_jour = fields.Date.context_today(self)
            _seen_pairs = set()
            _dedup_count = 0

            def get_cell(row, col_name):
                if not col_name:
                    return ""
                cn = (col_name or "").strip()
                # Virtual columns for mapping
                lc = cn.lower()
                if lc == "ref_clean":
                    return ref_clean
                if lc in ("date_du_jour", "date du jour", "today"):
                    try:
                        return fields.Date.to_string(date_du_jour)
                    except Exception:
                        return str(date_du_jour) or ""
                idx = hdr_index.get(cn)
                if idx is None or idx >= len(row):
                    return ""
                return (row[idx] or "").strip()

            Product = self.env["product.product"].with_company(provider.company_id)
            Template = self.env["product.template"].with_company(provider.company_id)

            def find_product_by_ean(ean):
                prods = Product.search([("barcode", "=", ean)])
                if len(prods) > 1 and provider.clear_duplicate_barcodes:
                    try:
                        prods.write({"barcode": False})
                    except Exception:
                        pass
                    return self.env["product.product"]
                return prods[:1]

            def find_product_by_vendor_ref(vref):
                if not vref:
                    return self.env["product.product"]
                prods = Product.search([("default_code", "=", vref)])
                return prods[:1]

            not_found_count = 0
            no_write_or_empty_count = 0
            for row in data_iter:
                total += 1
                ean = get_cell(row, (mapping.get("ean") or "").strip())
                vendor_ref_val = get_cell(row, (mapping.get("vendor_ref") or "").strip())

                # Intra-file dedup by (normalized reference, barcode)
                ref_for_pair = vendor_ref_val or ref_clean
                norm_ref = re.sub(r"[^A-Za-z0-9]", "", (ref_for_pair or "")).upper()
                if ean and norm_ref:
                    _pair = (norm_ref, ean)
                    if _pair in _seen_pairs:
                        _dedup_count += 1
                        continue
                    _seen_pairs.add(_pair)

                prod = self.env["product.product"]
                if ean:
                    prod = find_product_by_ean(ean)
                # Fallback: try vendor reference if no product by EAN
                if not prod and vendor_ref_val:
                    prod = find_product_by_vendor_ref(vendor_ref_val)
                if not prod:
                    error_count += 1
                    not_found_count += 1
                    continue
                tmpl = prod.product_tmpl_id

                writes_tmpl = {}
                writes_prod = {}

                # Product name
                name_val = get_cell(row, (mapping.get("product_name") or "").strip())
                if name_val:
                    writes_tmpl["name"] = name_val

                # Purchase price -> standard_price
                pprice_val = get_cell(row, (mapping.get("purchase_price") or "").strip())
                pprice = parse_float_val(pprice_val)
                if pprice is not None:
                    writes_tmpl["standard_price"] = pprice

                # Vendor reference -> default_code on variant
                vendor_ref = get_cell(row, (mapping.get("vendor_ref") or "").strip())
                if vendor_ref:
                    writes_prod["default_code"] = vendor_ref

                # VAT rate -> set taxes_id to matching tax by rate (sale tax for simplicity)
                vat_raw = get_cell(row, (mapping.get("vat_rate") or "").strip())
                vat = parse_float_val(vat_raw)
                if vat is not None:
                    try:
                        tax = self.env["account.tax"].with_company(provider.company_id).search(
                            [("amount", "=", vat), ("type_tax_use", "in", ["sale", "none"]), ("company_id", "=", provider.company_id.id)],
                            limit=1
                        )
                        if tax:
                            tmpl.write({"taxes_id": [(6, 0, [tax.id])]})
                    except Exception:
                        pass

                # Category by name
                categ_name = get_cell(row, (mapping.get("category") or "").strip())
                if categ_name:
                    try:
                        categ = self.env["product.category"].search([("name", "=", categ_name)], limit=1)
                        if categ:
                            writes_tmpl["categ_id"] = categ.id
                    except Exception:
                        pass

                # Brand by name (if module/field exists)
                # ✅ FIX: Accepter AUSSI "product_brand_id" comme clé de mapping (en plus de "brand")
                brand_col = (mapping.get("brand") or mapping.get("product_brand_id") or "").strip()
                brand_name = get_cell(row, brand_col)
                if brand_name:
                    try:
                        if "product.brand" in self.env:
                            brand = self.env["product.brand"].search([("name", "=ilike", brand_name)], limit=1)
                            if not brand:
                                # Auto-créer la marque si elle n'existe pas
                                brand = self.env["product.brand"].create({"name": brand_name})
                                _logger.info("[MAPPING] Auto-created brand '%s' (id=%d)", brand_name, brand.id)
                            # ✅ FIX: Écrire dans product_brand_id (le vrai nom du champ)
                            if brand:
                                if "product_brand_id" in tmpl._fields:
                                    writes_tmpl["product_brand_id"] = brand.id
                                elif hasattr(tmpl, "brand_id"):
                                    writes_tmpl["brand_id"] = brand.id
                    except Exception as e:
                        _logger.warning("[MAPPING] Brand lookup failed for '%s': %s", brand_name, e)

                # Extra mappings (user-defined)
                # ✅ FIX: Gérer correctement les champs Many2one (lookup par nom au lieu d'écrire une string)
                try:
                    for ex in (mapping.get("extra") or []):
                        tgt = (ex.get("target") or "").strip()
                        col = (ex.get("column") or "").strip()
                        if not tgt or not col:
                            continue
                        val = get_cell(row, col)
                        if val == "":
                            continue
                        field_name = tgt.replace(" ", "_")
                        
                        # Déterminer le modèle et le champ
                        target_fields = tmpl._fields if field_name in tmpl._fields else (prod._fields if field_name in prod._fields else None)
                        if target_fields is None:
                            continue
                        
                        field_obj = target_fields.get(field_name)
                        if field_obj and field_obj.type == "many2one":
                            # ✅ FIX: Champ Many2one → lookup par nom dans le modèle lié
                            comodel_name = field_obj.comodel_name
                            try:
                                related_rec = self.env[comodel_name].search([("name", "=ilike", val)], limit=1)
                                if not related_rec:
                                    # Auto-créer si possible (marques, catégories...)
                                    try:
                                        related_rec = self.env[comodel_name].create({"name": val})
                                        _logger.info("[MAPPING] Auto-created %s '%s' (id=%d)", comodel_name, val, related_rec.id)
                                    except Exception:
                                        _logger.warning("[MAPPING] Cannot auto-create %s '%s'", comodel_name, val)
                                        continue
                                if field_name in tmpl._fields:
                                    writes_tmpl[field_name] = related_rec.id
                                else:
                                    writes_prod[field_name] = related_rec.id
                            except Exception as e:
                                _logger.warning("[MAPPING] Many2one lookup failed for %s='%s': %s", field_name, val, e)
                        elif field_obj and field_obj.type in ("integer",):
                            try:
                                if field_name in tmpl._fields:
                                    writes_tmpl[field_name] = int(float(val))
                                else:
                                    writes_prod[field_name] = int(float(val))
                            except (ValueError, TypeError):
                                pass
                        elif field_obj and field_obj.type in ("float", "monetary"):
                            try:
                                parsed = parse_float_val(val)
                                if parsed is not None:
                                    if field_name in tmpl._fields:
                                        writes_tmpl[field_name] = parsed
                                    else:
                                        writes_prod[field_name] = parsed
                            except Exception:
                                pass
                        else:
                            # Champ texte ou autre → écrire la valeur telle quelle
                            if field_name in tmpl._fields:
                                writes_tmpl[field_name] = val
                            elif field_name in prod._fields:
                                writes_prod[field_name] = val
                except Exception:
                    pass

                # Apply writes
                wrote = False
                if writes_tmpl:
                    try:
                        tmpl.write(writes_tmpl)
                        wrote = True
                    except Exception:
                        error_count += 1
                        continue
                if writes_prod:
                    try:
                        prod.write(writes_prod)
                        wrote = True
                    except Exception:
                        error_count += 1
                        continue

                if wrote:
                    ok_count += 1
                else:
                    # No mapped field applied -> count as error
                    error_count += 1
                    no_write_or_empty_count += 1

        # Append summary with breakdown
        summary = _("<p>(Mapping) Total lines: %d</p><p>Updated products: %d</p><p>Errors: %d</p>") % (total, ok_count, error_count)
        try:
            summary += _("<p>Not found products: %d</p><p>No write on found product: %d</p>") % (not_found_count, no_write_or_empty_count)
        except Exception:
            pass
        rules_html = (_("<h5>Règles appliquées</h5><p>ref_clean: %s</p><p>date_du_jour: %s</p><p>Doublons ignorés (réf+code-barres): %d</p>") % (html.escape(ref_clean or ""), html.escape(str(date_du_jour) or ""), _dedup_count))
        log.write({"log_html": (log.log_html or "") + rules_html + summary})
        return total, ok_count, error_count

    # ---------------------------
    # Core logic
    # ---------------------------
    def _process_single_file(self, provider, remote_path, archive=False):
        Log = self.env["ftp.tariff.import.log"].with_company(provider.company_id)
        backend = self.env["ftp.backend.service"]
        log = Log.create({
            "name": _("Tariff Import"),
            "provider_id": provider.id,
            "company_id": provider.company_id.id,
            "protocol": (provider.protocol or "").lower(),
            "file_name": remote_path,
        })
        log.mark_started()
        # Snapshot provider info and remote file mtime (best-effort) for standard flow
        try:
            files_info = backend.list_provider_files(provider)
            info = next((f for f in files_info if f.get("path") == remote_path), None)
            if info and info.get("mtime"):
                ts = float(info.get("mtime"))
                dt = fields.Datetime.to_string(datetime.fromtimestamp(ts, timezone.utc))
                log.write({"remote_mtime": dt})
        except Exception:
            pass

        local_path = None
        try:
            local_path, size = backend.download_to_temp(provider, remote_path)
            # Attach original file to log for local download
            try:
                with open(local_path, "rb") as bf:
                    b64 = base64.b64encode(bf.read())
                log.write({"file_data": b64, "file_data_name": os.path.basename(remote_path)})
            except Exception as att_e:
                _logger.warning("Could not attach source file to log: %s", att_e)
            total, ok_count, error_count = self._import_csv_file(provider, local_path, log)
            log.mark_done(total=total, success=ok_count, error=error_count, msg=_("Imported"))
            try:
                if (provider.protocol or "").lower() == "imap":
                    moved = False
                    if getattr(provider, "imap_move_processed", False):
                        try:
                            new_path = backend.move_remote(provider, remote_path, provider.remote_dir_processed or "Processed")
                            moved = True
                        except Exception as me:
                            _logger.warning("IMAP move on success failed for %s: %s", remote_path, me)
                    if moved:
                        log.write({"log_html": (log.log_html or "") + (_("<p>Imported and moved to mailbox: %s</p>") % (provider.remote_dir_processed or "Processed"))})
                    else:
                        # Optionally mark message as Seen when not moved
                        if getattr(provider, "imap_mark_seen", False):
                            try:
                                if backend.mark_seen(provider, remote_path):
                                    log.write({"log_html": (log.log_html or "") + (_("<p>Message marked as Seen.</p>"))})
                            except Exception as se:
                                _logger.warning("IMAP mark seen failed for %s: %s", remote_path, se)
                        log.write({"log_html": (log.log_html or "") + (_("<p>No remote move performed; message left in place.</p>"))})
                else:
                    log.write({"log_html": (log.log_html or "") + (_("<p>No remote move performed; file left in place.</p>"))})
            except Exception:
                pass
        except Exception as e:
            log.mark_error(msg=str(e))
            try:
                if (provider.protocol or "").lower() == "imap" and getattr(provider, "imap_move_error", False):
                    try:
                        backend.move_remote(provider, remote_path, provider.remote_dir_error or "Error")
                        log.write({"log_html": (log.log_html or "") + (_("<p>Error: message moved to mailbox: %s</p>") % (provider.remote_dir_error or "Error"))})
                    except Exception as me:
                        _logger.warning("IMAP move on error failed for %s: %s", remote_path, me)
                        log.write({"log_html": (log.log_html or "") + (_("<p>No remote move performed; message left in place (error path).</p>"))})
                else:
                    log.write({"log_html": (log.log_html or "") + (_("<p>No remote move performed; file left in place (error path).</p>"))})
            except Exception:
                pass
            _logger.exception("Tariff import failed for %s", remote_path)
        finally:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass
        return log

    def _import_csv_file(self, provider, local_path, log):
        """Stream the CSV and update product template list_price.

        Rules:
        - Identify product by barcode (first non-empty among configured columns).
        - If DB has duplicate products sharing the same barcode, clear barcodes and skip these lines in this run.
        - Price read from configured column; decimal separator supported; UTF-8 csv.
        """
        params = provider.get_csv_reader_params()
        delimiter = params["delimiter"]
        has_header = params["has_header"]
        dec_sep = params["decimal_separator"] or "."
        encoding = params["encoding"] or "utf-8"

        barcode_candidates = provider.get_barcode_candidates()
        price_col_name = provider.get_price_column()

        def parse_price(raw):
            if raw is None:
                return None
            s = str(raw).strip()
            if s == "":
                return None
            if dec_sep != ".":
                s = s.replace(dec_sep, ".")
            # remove spaces in numbers like "1 234,56"
            s = s.replace(" ", "")
            try:
                return float(s)
            except Exception:
                return None

        total = 0
        ok_count = 0
        error_count = 0

        # We will batch resolve barcodes by chunks
        CHUNK = 2000
        pending_rows = []

        def flush_chunk(rows):
            nonlocal ok_count, error_count
            if not rows:
                return
            # Resolve barcodes
            barcodes = [r["barcode"] for r in rows if r["barcode"]]
            if not barcodes:
                return
            Product = self.env["product.product"].with_company(provider.company_id)
            # Find duplicates in DB by grouping
            # Search all products with these barcodes
            products = Product.search([("barcode", "in", list(set(barcodes)))])
            # Map barcode -> product.product records
            bc_map = {}
            for p in products:
                bc_map.setdefault(p.barcode, self.env["product.product"])
                bc_map[p.barcode] += p

            # Detect duplicates and clear if required
            duplicates = [bc for bc, recs in bc_map.items() if len(recs) > 1]
            if duplicates and provider.clear_duplicate_barcodes:
                # clear barcode on those products to enforce uniqueness
                for bc in duplicates:
                    try:
                        bc_map[bc].write({"barcode": False})
                    except Exception:
                        pass
                # remove from mapping (so lines with those barcodes are skipped this run)
                for bc in duplicates:
                    bc_map.pop(bc, None)

            # Aggregate last price per product.template
            tmpl_last_price = {}
            for r in rows:
                bc = r["barcode"]
                price = r["price"]
                if not bc or price is None:
                    error_count += 1
                    continue
                recs = bc_map.get(bc)
                if not recs or len(recs) != 1:
                    # Not found or still duplicate
                    error_count += 1
                    continue
                tmpl_id = recs.product_tmpl_id.id
                tmpl_last_price[tmpl_id] = price  # last wins

            # Apply writes per template (individual to support different prices)
            if tmpl_last_price:
                Template = self.env["product.template"].with_company(provider.company_id)
                for tmpl_id, price in tmpl_last_price.items():
                    try:
                        Template.browse(tmpl_id).write({"list_price": price})
                        ok_count += 1
                    except Exception:
                        error_count += 1

        # ✅ FIX ENCODAGE: Lire le fichier ENTIER en binaire pour tester chaque encoding
        # (avant: seulement 8192 octets → les accents après cette position étaient corrompus)
        enc_candidates = [encoding] if encoding else []
        for e in ("utf-8-sig", "cp1252", "latin-1"):
            if e not in enc_candidates:
                enc_candidates.append(e)
        selected_enc = None
        try:
            with open(local_path, "rb") as bf:
                raw_bytes_std = bf.read()
        except Exception:
            raw_bytes_std = b""
        for enc_try in enc_candidates:
            try:
                raw_bytes_std.decode(enc_try)
                selected_enc = enc_try
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if not selected_enc:
            selected_enc = encoding or "utf-8"
        if selected_enc != encoding:
            _logger.info("[IMPORT] Encoding auto-detected: %s (configured: %s)", selected_enc, encoding)
            try:
                log.write({"log_html": (log.log_html or "") + (_("<p>Notice: encodage détecté: <b>%s</b> (configuré: %s)</p>") % (selected_enc, encoding))})
            except Exception:
                pass
        with open(local_path, "r", encoding=selected_enc, errors="replace", newline="") as f:
            headers = []
            if delimiter and len(delimiter) == 1:
                reader = csv.reader(f, delimiter=delimiter)
                if has_header:
                    try:
                        headers = next(reader)
                    except StopIteration:
                        headers = []
                data_iter = reader
            else:
                if has_header:
                    first_line = f.readline()
                    headers = [] if first_line == "" else first_line.rstrip("\r\n").split(delimiter)
                # iterator over remaining lines
                def _iter_lines(_f):
                    for _line in _f:
                        yield _line.rstrip("\r\n").split(delimiter)
                data_iter = _iter_lines(f)
            # Normalize headers to map
            hdr_index = {h.strip(): idx for idx, h in enumerate(headers)}
            hdr_lc_index = {(h.strip() or "").lower(): idx for idx, h in enumerate(headers)}
            # Rules context
            try:
                _fname = os.path.basename(log.file_name or "") or os.path.basename(local_path or "")
            except Exception:
                _fname = os.path.basename(local_path or "")
            _base = _fname.rsplit(".", 1)[0]
            ref_clean = re.sub(r"[^A-Za-z0-9]", "", _base)
            date_du_jour = fields.Date.context_today(self)
            _seen_pairs = set()
            _dedup_count = 0

            # Helper to extract barcode/price/reference from a row
            def extract_row(row):
                barcode = None
                price = None
                ref_val = None
                if headers:
                    # Try barcode candidates by header names (case-insensitive)
                    for name in barcode_candidates:
                        idx = hdr_index.get(name)
                        if idx is None:
                            idx = hdr_lc_index.get((name or "").strip().lower())
                        if idx is not None and idx < len(row):
                            v = (row[idx] or "").strip()
                            if v:
                                barcode = v
                                break
                    # Price
                    idxp = hdr_index.get(price_col_name) or hdr_lc_index.get((price_col_name or "").strip().lower())
                    if idxp is not None and idxp < len(row):
                        price = parse_price(row[idxp])
                    # Reference by common synonyms
                    ref_synonyms = ["référence", "reference", "réf", "ref", "default_code", "sku", "code article", "article", "reference fournisseur"]
                    ref_idx = None
                    for nm in ref_synonyms:
                        if nm in hdr_index:
                            ref_idx = hdr_index.get(nm)
                        else:
                            ref_idx = hdr_lc_index.get(nm)
                        if ref_idx is not None:
                            break
                    if ref_idx is not None and ref_idx < len(row):
                        ref_val = (row[ref_idx] or "").strip()
                else:
                    # No header: assume first non-empty column is barcode, second is price
                    for c in row:
                        s = (c or "").strip()
                        if s and barcode is None:
                            barcode = s
                            continue
                        if s and price is None:
                            price = parse_price(s)
                            break
                    # No header -> ref from filename
                # Fallback for ref
                if not ref_val:
                    ref_val = ref_clean
                # Normalized reference (alnum only, uppercase)
                norm_ref = re.sub(r"[^A-Za-z0-9]", "", ref_val or "").upper()
                return barcode, price, norm_ref

            for row in data_iter:
                total += 1
                barcode, price, norm_ref = extract_row(row)
                # Take the first non-empty barcode found among candidate columns
                if not barcode and headers:
                    # Try additional candidate headers if not found in first pass
                    for h in headers:
                        if "code barre" in h.lower() and not barcode:
                            idx = hdr_index.get(h)
                            if idx is None:
                                idx = hdr_lc_index.get(h.lower())
                            if idx is not None and idx < len(row):
                                v = (row[idx] or "").strip()
                                if v:
                                    barcode = v
                                    break
                # Intra-file deduplication by (reference, barcode)
                if barcode and norm_ref:
                    _pair = (norm_ref, barcode)
                    if _pair in _seen_pairs:
                        _dedup_count += 1
                        continue
                    _seen_pairs.add(_pair)
                # Register row
                pending_rows.append({"barcode": barcode, "price": price})
                if len(pending_rows) >= CHUNK:
                    flush_chunk(pending_rows)
                    pending_rows = []
            # flush remaining
            flush_chunk(pending_rows)

        # Log summary table (simple)
        rules_html = (_("<h5>Règles appliquées</h5><p>ref_clean: %s</p><p>date_du_jour: %s</p><p>Doublons ignorés (réf+code-barres): %d</p>") % (html.escape(ref_clean or ""), html.escape(str(date_du_jour) or ""), _dedup_count))
        summary = _("<p>Total lines: %d</p><p>Updated products: %d</p><p>Errors: %d</p>") % (total, ok_count, error_count)
        log.write({"log_html": (log.log_html or "") + rules_html + summary})
        return total, ok_count, error_count
