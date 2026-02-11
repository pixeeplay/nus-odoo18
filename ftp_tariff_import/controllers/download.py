# -*- coding: utf-8 -*-
import os
import posixpath
import tempfile
import zipfile
from datetime import datetime

from odoo import http, _
from odoo.http import request, content_disposition
from odoo.exceptions import AccessError

import logging

_logger = logging.getLogger(__name__)


class FtpTariffDownloadController(http.Controller):
    @http.route("/ftp_tariff_import/download/<int:wizard_id>", type="http", auth="user")
    def download_selected(self, wizard_id, **kwargs):
        """Stream selected file(s) from the preview wizard to the browser.

        - If one file selected: stream that file directly with original basename.
        - If multiple files: zip them on-the-fly in a temp file and stream the ZIP.
        """
        # Security: user must belong to our groups
        user = request.env.user
        if not (user.has_group("ftp_tariff_import.group_ftp_tariff_user") or
                user.has_group("ftp_tariff_import.group_ftp_tariff_manager")):
            raise AccessError(_("You do not have permission to download FTP files."))

        Wizard = request.env["ftp.preview.wizard"].sudo()  # sudo to ensure transient readability
        wiz = Wizard.browse(wizard_id)
        if not wiz.exists():
            return request.not_found()

        provider = wiz.provider_id
        # Additional multi-company safety: restrict by company visibility
        # (preview wizard creation already enforced this)
        if provider.company_id and provider.company_id.id not in user.company_ids.ids:
            raise AccessError(_("You cannot access files for this company."))

        # Collect selected paths
        paths = [l.remote_path for l in wiz.line_ids if l.checked and l.remote_path]
        if not paths:
            # fallback: if none checked, try first line
            first = wiz.line_ids[:1]
            if first:
                paths = [first.remote_path]
        if not paths:
            return request.make_response(
                _("No file selected."),
                headers=[("Content-Type", "text/plain; charset=utf-8")]
            )

        backend = request.env["ftp.backend.service"].with_user(user).with_company(provider.company_id)

        # Single file case
        if len(paths) == 1:
            remote_path = paths[0]
            local_path = None
            try:
                local_path, size = backend.download_to_temp(provider, remote_path)
                # Guess filename
                filename = posixpath.basename(remote_path) or "file.csv"
                with open(local_path, "rb") as f:
                    content = f.read()
                headers = [
                    ("Content-Type", "text/csv; charset=utf-8"),
                    ("Content-Disposition", content_disposition(filename)),
                ]
                return request.make_response(content, headers=headers)
            finally:
                if local_path and os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass

        # Multiple files -> ZIP
        tmp_zip = None
        downloaded = []
        try:
            # Download all to temp and collect tuples (basename, path)
            for rp in paths:
                lp, _ = backend.download_to_temp(provider, rp)
                downloaded.append((posixpath.basename(rp) or "file.csv", lp))

            # Build ZIP
            tmp_zip = tempfile.NamedTemporaryFile(prefix="ftp_dl_", suffix=".zip", delete=False)
            tmp_zip_path = tmp_zip.name
            tmp_zip.close()

            with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for base, local in downloaded:
                    arcname = base
                    # Ensure unique names if duplicates
                    if any(i for i in zf.namelist() if i == arcname):
                        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                        arcname = f"{ts}_{arcname}"
                    zf.write(local, arcname)

            # Stream ZIP
            with open(tmp_zip_path, "rb") as fz:
                content = fz.read()
            zip_name = "ftp_files_%s.zip" % datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            headers = [
                ("Content-Type", "application/zip"),
                ("Content-Disposition", content_disposition(zip_name)),
            ]
            return request.make_response(content, headers=headers)
        finally:
            # Cleanup
            for _, lp in downloaded:
                try:
                    if lp and os.path.exists(lp):
                        os.remove(lp)
                except Exception:
                    pass
            if tmp_zip and os.path.exists(tmp_zip.name):
                try:
                    os.remove(tmp_zip.name)
                except Exception:
                    pass

    @http.route("/ftp_tariff_import/download_merged/<int:wizard_id>", type="http", auth="user")
    def download_merged(self, wizard_id, **kwargs):
        """Download selected files, merge them into a single CSV, and stream to browser.
        
        For multi-file providers like TD Synnex (MaterialFile + StockFile + TaxesGouv).
        Uses the provider's multi-file configuration to merge files on the merge_key column.
        """
        import fnmatch
        
        # Security: user must belong to our groups
        user = request.env.user
        if not (user.has_group("ftp_tariff_import.group_ftp_tariff_user") or
                user.has_group("ftp_tariff_import.group_ftp_tariff_manager")):
            raise AccessError(_("You do not have permission to download FTP files."))

        Wizard = request.env["ftp.preview.wizard"].sudo()
        wiz = Wizard.browse(wizard_id)
        if not wiz.exists():
            return request.not_found()

        provider = wiz.provider_id
        if provider.company_id and provider.company_id.id not in user.company_ids.ids:
            raise AccessError(_("You cannot access files for this company."))

        # Check multi-file mode
        if not getattr(provider, 'multi_file_mode', False):
            return request.make_response(
                _("Le mode multi-fichiers n'est pas activé pour ce fournisseur."),
                headers=[("Content-Type", "text/plain; charset=utf-8")]
            )

        # Collect selected paths
        paths = [l.remote_path for l in wiz.line_ids if l.checked and l.remote_path]
        if not paths:
            return request.make_response(
                _("Aucun fichier sélectionné."),
                headers=[("Content-Type", "text/plain; charset=utf-8")]
            )

        backend = request.env["ftp.backend.service"].with_user(user).with_company(provider.company_id)
        
        # Import merger from planete_pim module
        from odoo.addons.planete_pim.models.multi_file_merger import merge_provider_files
        
        local_files = []
        tmp_merged = None
        
        try:
            # 1. Download all selected files
            for remote_path in paths:
                local_path, _ = backend.download_to_temp(provider, remote_path)
                local_files.append((remote_path, local_path))
            
            # 2. Identify files by pattern
            material_pattern = provider.file_pattern_material or "MaterialFile*.txt"
            stock_pattern = provider.file_pattern_stock or "StockFile*.txt"
            taxes_pattern = provider.file_pattern_taxes or "TaxesGouv*.txt"
            
            material_content = None
            stock_content = None
            taxes_content = None
            
            for remote_path, local_path in local_files:
                filename = posixpath.basename(remote_path)
                
                with open(local_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                
                if fnmatch.fnmatch(filename, material_pattern):
                    material_content = content
                    _logger.info("[DOWNLOAD MERGED] Found Material file: %s", filename)
                elif fnmatch.fnmatch(filename, stock_pattern):
                    stock_content = content
                    _logger.info("[DOWNLOAD MERGED] Found Stock file: %s", filename)
                elif fnmatch.fnmatch(filename, taxes_pattern):
                    taxes_content = content
                    _logger.info("[DOWNLOAD MERGED] Found Taxes file: %s", filename)
            
            if not material_content:
                return request.make_response(
                    _("Fichier Material non trouvé! Pattern attendu: %s") % material_pattern,
                    headers=[("Content-Type", "text/plain; charset=utf-8")]
                )
            
            # 3. Merge files
            tmp_merged, headers = merge_provider_files(provider, material_content, stock_content, taxes_content)
            
            if not tmp_merged:
                return request.make_response(
                    _("La fusion a échoué."),
                    headers=[("Content-Type", "text/plain; charset=utf-8")]
                )
            
            # 4. Read merged file and stream
            with open(tmp_merged, 'rb') as f:
                content = f.read()
            
            filename = "%s_merged_%s.csv" % (
                provider.name or "provider",
                datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            )
            
            headers = [
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", content_disposition(filename)),
            ]
            return request.make_response(content, headers=headers)
            
        finally:
            # Cleanup downloaded files
            for _, local_path in local_files:
                if local_path and os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass
            # Cleanup merged file
            if tmp_merged and os.path.exists(tmp_merged):
                try:
                    os.remove(tmp_merged)
                except Exception:
                    pass
