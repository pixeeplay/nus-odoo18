# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
from datetime import datetime, timedelta
try:
    import pytz  # timezone conversions for cron nextcall
except Exception:
    pytz = None

_logger = logging.getLogger(__name__)


class FtpProvider(models.Model):
    _name = "ftp.provider"
    _description = "FTP/SFTP/IMAP Provider for Tariff Import"
    _rec_name = "name"

    # Basic
    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company.id,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Fournisseur",
        help="Fournisseur lié créé automatiquement à la création du provider.",
        ondelete="set null",
    )

    # Connection
    protocol = fields.Selection(
        selection=[("ftp", "FTP"), ("sftp", "SFTP"), ("imap", "IMAP"), ("gdrive", "Google Drive"), ("local", "Fichier Local"), ("url", "URL (HTTP/HTTPS)")],
        required=True,
        default="sftp",
    )
    host = fields.Char(required=False, help="Hostname (not required for Google Drive, Local or URL)")
    
    # URL options
    url = fields.Char(
        string="URL du fichier",
        help="URL complète du fichier à télécharger (ex: https://example.com/files/prices.csv). Supporte HTTP et HTTPS."
    )
    url_username = fields.Char(
        string="Username URL (optionnel)",
        help="Nom d'utilisateur pour l'authentification HTTP Basic (si requis)."
    )
    url_password = fields.Char(
        string="Password URL (optionnel)",
        help="Mot de passe pour l'authentification HTTP Basic (si requis)."
    )
    
    # Local filesystem options
    local_path = fields.Char(
        string="Chemin local",
        help="Chemin absolu vers le dossier local contenant les fichiers à importer. Ex: /opt/odoo/imports ou C:\\Imports"
    )
    port = fields.Integer(string="Port", default=22)
    username = fields.Char(string="Username")
    password = fields.Char(string="Password")
    ftp_passive = fields.Boolean(string="FTP Passive Mode", default=True)
    ftp_use_tls = fields.Boolean(
        string="FTP TLS/SSL (FTPS)",
        default=False,
        help="Activer pour les serveurs FTPS (FTP over TLS/SSL). "
             "La plupart des serveurs FTP modernes exigent TLS. "
             "Si FileZilla fonctionne mais pas Odoo, activez cette option."
    )
    timeout = fields.Integer(default=60, help="Socket timeout in seconds.")
    retries = fields.Integer(default=3)
    keepalive = fields.Integer(default=30, help="Keepalive (SFTP) in seconds.")
    sftp_hostkey_fingerprint = fields.Char(
        help="Optional host key fingerprint to validate SFTP server."
    )

    # SFTP private key (optional)
    sftp_pkey_content = fields.Text(
        string="SFTP Private Key (PEM)",
        help="Paste PEM private key content if required for SFTP auth."
    )
    sftp_pkey_passphrase = fields.Char(string="SFTP Private Key Passphrase")

    # Google Drive OAuth 2.0 options
    gdrive_client_id = fields.Char(
        string="Client ID OAuth",
        help="Client ID from Google Cloud Console OAuth 2.0 credentials."
    )
    gdrive_client_secret = fields.Char(
        string="Client Secret OAuth",
        help="Client Secret from Google Cloud Console OAuth 2.0 credentials."
    )
    gdrive_refresh_token = fields.Char(
        string="Refresh Token",
        help="OAuth refresh token (stored after authorization)."
    )
    gdrive_access_token = fields.Char(
        string="Access Token",
        help="OAuth access token (temporary, auto-refreshed)."
    )
    gdrive_token_expiry = fields.Datetime(
        string="Token Expiry",
        help="Expiration time of the current access token."
    )
    gdrive_folder_id = fields.Char(
        string="Folder ID (import)",
        help="Google Drive folder ID for importing files. Leave empty for root."
    )
    gdrive_export_folder_id = fields.Char(
        string="Folder ID (export)",
        help="Google Drive folder ID for exporting files."
    )
    gdrive_auth_state = fields.Char(
        string="OAuth State",
        help="Temporary state for OAuth flow."
    )
    gdrive_connected = fields.Boolean(
        string="Google Drive Connected",
        compute="_compute_gdrive_connected",
        store=False,
    )
    gdrive_redirect_uri = fields.Char(
        string="Redirect URI",
        compute="_compute_gdrive_redirect_uri",
        store=False,
        help="URI to configure in Google Cloud Console."
    )

    # Mapping template
    mapping_template_id = fields.Many2one(
        "ftp.mapping.template",
        string="Template de mapping",
        help="Template de mapping CSV vers champs product.template"
    )
    mapping_template_ids = fields.One2many(
        "ftp.mapping.template",
        "provider_id",
        string="Templates de mapping",
    )

    @api.depends("gdrive_refresh_token")
    def _compute_gdrive_connected(self):
        for rec in self:
            rec.gdrive_connected = bool(rec.gdrive_refresh_token)

    def _compute_gdrive_redirect_uri(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for rec in self:
            rec.gdrive_redirect_uri = f"{base_url}/gdrive/oauth/callback"

    def action_gdrive_authorize(self):
        """Redirect user to Google OAuth authorization page."""
        self.ensure_one()
        import secrets
        state = secrets.token_urlsafe(32)
        self.sudo().write({"gdrive_auth_state": state})
        
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        redirect_uri = f"{base_url}/gdrive/oauth/callback"
        scope = "https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive.readonly"
        
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={self.gdrive_client_id}&"
            f"redirect_uri={redirect_uri}&"
            f"response_type=code&"
            f"scope={scope}&"
            f"access_type=offline&"
            f"prompt=consent&"
            f"state={self.id}_{state}"
        )
        return {
            "type": "ir.actions.act_url",
            "url": auth_url,
            "target": "self",
        }

    def action_gdrive_disconnect(self):
        """Revoke Google Drive authorization."""
        self.ensure_one()
        self.sudo().write({
            "gdrive_refresh_token": False,
            "gdrive_access_token": False,
            "gdrive_token_expiry": False,
            "gdrive_auth_state": False,
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Déconnecté"),
                "message": _("Google Drive a été déconnecté."),
                "sticky": False,
            },
        }

    # IMAP options
    imap_use_ssl = fields.Boolean(
        string="IMAP SSL",
        default=True,
        help="Use SSL/TLS for IMAP (port 993). If disabled, use plaintext/STARTTLS (port 143)."
    )
    imap_search_criteria = fields.Char(
        string="IMAP Search Criteria",
        default="",
        help="IMAP SEARCH criteria. Ex: UNSEEN, SINCE 01-Jan-2025, ALL. Laisser vide pour ALL (récupère tous les emails y compris lus)."
    )
    imap_mark_seen = fields.Boolean(
        string="IMAP Mark Seen",
        default=False,
        help="Mark messages as Seen after successful processing when not moved. Désactivé par défaut pour les boîtes mail partagées."
    )
    imap_move_processed = fields.Boolean(
        string="IMAP Move on Success",
        default=False,
        help="Move message to the 'Processed' mailbox after success. Désactivé par défaut pour les boîtes mail partagées."
    )
    imap_move_error = fields.Boolean(
        string="IMAP Move on Error",
        default=False,
        help="Move message to the 'Error' mailbox on failure. Désactivé par défaut pour les boîtes mail partagées."
    )

    # Remote directories and patterns
    remote_dir_in = fields.Char(default="/", required=True)
    remote_dir_processed = fields.Char(default="/processed", required=True)
    remote_dir_error = fields.Char(default="/error", required=True)
    file_pattern = fields.Char(default="*", required=True, help="Pattern de fichier (ex: *.csv, *.xlsx). Par défaut: * = tous les fichiers")
    exclude_pattern = fields.Char(help="Optional pattern to exclude files.")

    # CSV options and mapping
    csv_encoding = fields.Selection(
        selection=[("auto", "Auto-detect"), ("utf-8", "UTF-8"), ("utf-8-sig", "UTF-8 with BOM"), ("cp1252", "Windows-1252"), ("latin-1", "Latin-1")],
        default="auto",
        required=True,
        string="CSV Encoding",
    )
    csv_delimiter = fields.Selection(
        selection=[
            (";", "Point-virgule (;) - Format français"),
            (",", "Virgule (,) - Format international"),
            ("\t", "Tabulation (Tab) - Fichiers TSV"),
            (" ", "Espace - Colones séparées par espace"),
            ("|", "Pipe (|) - Format alternatif"),
        ],
        default=";",
        required=True,
        string="Délimiteur CSV",
        help="Sélectionnez le délimiteur utilisé dans les fichiers CSV. Recommandé: Point-virgule (;) pour les fichiers français."
    )
    csv_has_header = fields.Boolean(default=True)
    decimal_separator = fields.Char(default=".", size=1, required=True)

    barcode_columns = fields.Char(
        string="Barcode Columns (ordered)",
        default="Code barre 1,Code barre 2,Code barre 3,Code barre 4,Code barre 5,Code barre 6,barcode,ean,ean13",
        help="Comma-separated list of column names to try for barcode, in order."
    )
    price_column = fields.Char(
        string="Price Column",
        default="Prix de vente",
        help="Column name to read price from."
    )

    # =========================================================================
    # MULTI-FILE MODE (ex: TD Synnex avec 3 fichiers à fusionner)
    # =========================================================================
    multi_file_mode = fields.Boolean(
        string="Mode multi-fichiers",
        default=False,
        help="Activer pour fusionner plusieurs fichiers avant import (ex: TD Synnex avec MaterialFile, StockFile, TaxesGouv)."
    )
    multi_file_merge_key = fields.Char(
        string="Colonne clé de fusion",
        default="Matnr",
        help="Nom de la colonne commune pour fusionner les fichiers (ex: Matnr pour TD Synnex)."
    )
    file_pattern_material = fields.Char(
        string="Pattern fichier principal",
        default="MaterialFile*.txt",
        help="Pattern du fichier principal (base de la fusion). Ex: MaterialFile*.txt"
    )
    file_pattern_stock = fields.Char(
        string="Pattern fichier stock",
        default="StockFile*.txt",
        help="Pattern du fichier stock (optionnel). Ex: StockFile*.txt"
    )
    file_pattern_taxes = fields.Char(
        string="Pattern fichier taxes",
        default="TaxesGouv*.txt",
        help="Pattern du fichier taxes (optionnel, format spécial sans header). Ex: TaxesGouv*.txt"
    )
    multi_file_material_delimiter = fields.Selection(
        selection=[
            ("\t", "Tabulation"),
            (";", "Point-virgule"),
            (",", "Virgule"),
            ("sap", "SAP (tab ou espaces multiples)"),
        ],
        string="Délimiteur fichier principal",
        default="sap",
        help="Délimiteur du fichier principal. 'SAP' gère automatiquement les tabulations ET les espaces multiples."
    )
    multi_file_stock_delimiter = fields.Selection(
        selection=[
            ("\t", "Tabulation"),
            (";", "Point-virgule"),
            (",", "Virgule"),
            ("sap", "SAP (tab ou espaces multiples)"),
        ],
        string="Délimiteur fichier stock",
        default="sap",
        help="Délimiteur du fichier stock."
    )

    # Execution options
    auto_process = fields.Boolean(
        default=True,
        help="If enabled, this provider will be processed by the daily cron."
    )
    schedule_level = fields.Selection(
        selection=[("full", "Complet"), ("rapid", "Rapide")],
        default="full",
        string="Niveau de planification",
        help="Détermine le type d'import exécuté par la planification: Complet ou Rapide."
    )
    max_files_per_run = fields.Integer(
        help="Optional max number of files to process per run (empty = no limit)."
    )
    max_preview = fields.Integer(
        default=500, help="Max files listed in preview wizard."
    )
    max_preview_rows = fields.Integer(
        default=200, help="Max CSV rows shown in preview."
    )
    clear_duplicate_barcodes = fields.Boolean(
        default=True,
        help="If several products share same barcode, clear barcode on them before import."
    )

    # Scheduling
    schedule_active = fields.Boolean(
        string="Activer la planification",
        default=False,
        help="Active un cron dédié pour ce fournisseur."
    )
    schedule_interval_type = fields.Selection(
        selection=[("hours", "Heures"), ("days", "Jours")],
        default="days",
        string="Intervalle"
    )
    schedule_interval_number = fields.Integer(
        default=1,
        string="Toutes les",
        help="Nombre d'unités d'intervalle entre deux exécutions."
    )
    schedule_time = fields.Float(
        string="Heure d'exécution (locale)",
        default=7.0,
        help="Heure locale d'exécution quotidienne. Exemple: 7.5 = 07:30."
    )
    schedule_timezone = fields.Char(
        string="Fuseau horaire (planification)",
        default=lambda self: (self.env.user.tz or "UTC"),
        help="Ex: Europe/Paris. Si vide, utilise le fuseau de l'utilisateur Odoo, sinon UTC."
    )
    schedule_cron_id = fields.Many2one(
        "ir.cron",
        string="Action planifiée liée",
        readonly=True,
        ondelete="set null",
        help="Cron dédié synchronisé depuis cet onglet."
    )

    # PIM Scheduling
    schedule_pim_active = fields.Boolean(
        string="Activer la planification PIM",
        default=False,
        help="Active un cron dédié PIM pour ce fournisseur."
    )
    schedule_pim_level = fields.Selection(
        selection=[("full", "Complet"), ("rapid", "Rapide")],
        default="rapid",
        string="Niveau PIM",
        help="Détermine le type d'import PIM exécuté par la planification."
    )
    schedule_pim_interval_type = fields.Selection(
        selection=[("hours", "Heures"), ("days", "Jours")],
        default="days",
        string="Intervalle PIM"
    )
    schedule_pim_interval_number = fields.Integer(
        default=1,
        string="Toutes les (PIM)",
        help="Nombre d'unités d'intervalle entre deux exécutions PIM."
    )
    schedule_pim_time = fields.Float(
        string="Heure d'exécution PIM (locale)",
        default=7.0,
        help="Heure locale d'exécution quotidienne PIM. Exemple: 7.5 = 07:30."
    )
    schedule_pim_timezone = fields.Char(
        string="Fuseau horaire PIM",
        default=lambda self: (self.env.user.tz or "UTC"),
        help="Ex: Europe/Paris. Si vide, utilise le fuseau de l'utilisateur Odoo, sinon UTC."
    )
    schedule_pim_cron_id = fields.Many2one(
        "ir.cron",
        string="Action planifiée PIM",
        readonly=True,
        ondelete="set null",
        help="Cron PIM dédié synchronisé depuis cet onglet."
    )

    # Status/journal
    last_connection_status = fields.Selection(
        selection=[("running", "Running"), ("ok", "OK"), ("failed", "Failed")],
        readonly=True
    )
    last_error = fields.Text(readonly=True)
    last_run_at = fields.Datetime(readonly=True)

    # Convenience computed/constraints
    @api.constrains("protocol", "port")
    def _check_port_default(self):
        # Don't do any writes in constraints - it causes recursion
        # Port defaults are handled in create() and onchange()
        pass

    def _compute_default_port(self, protocol=None, imap_use_ssl=None):
        p = (protocol or self.protocol or "sftp")
        ssl = self.imap_use_ssl if imap_use_ssl is None else imap_use_ssl
        if p == "ftp":
            return 21
        if p == "sftp":
            return 22
        if p == "imap":
            return 993 if ssl else 143
        return 0

    @api.onchange("protocol", "imap_use_ssl")
    def _onchange_protocol_set_port(self):
        for rec in self:
            try:
                rec.port = rec._compute_default_port()
            except Exception:
                # non-blocking in onchange
                pass

    def _action_open_wizard(self, wizard_model, view_xml_id=None, context_extra=None):
        self.ensure_one()
        ctx = dict(
            self.env.context,
            default_provider_id=self.id,
        )
        if context_extra:
            ctx.update(context_extra)
        action = {
            "type": "ir.actions.act_window",
            "res_model": wizard_model,
            "view_mode": "form",
            "target": "current",
            "context": ctx,
        }
        if view_xml_id:
            action["views"] = [(self.env.ref(view_xml_id).id, "form")]
        return action

    # Buttons
    def action_test_connection(self):
        """Try to connect and list files; open preview wizard with results."""
        self.ensure_one()
        return self._action_open_wizard(
            "ftp.preview.wizard",
            view_xml_id="ftp_tariff_import.view_ftp_preview_wizard",
            context_extra={"test_connection": True},
        )

    def action_open_preview(self):
        self.ensure_one()
        return self._action_open_wizard(
            "ftp.preview.wizard",
            view_xml_id="ftp_tariff_import.view_ftp_preview_wizard",
        )

    def action_import_now(self):
        """Open manual import wizard."""
        self.ensure_one()
        return self._action_open_wizard(
            "ftp.import.wizard",
            view_xml_id="ftp_tariff_import.view_ftp_import_wizard",
        )

    def action_open_export_wizard(self):
        """Open export wizard."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Exporter les données"),
            "res_model": "ftp.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": dict(self.env.context),
        }

    def action_view_logs(self):
        """Open logs filtered on this provider."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Journaux d'import"),
            "res_model": "ftp.tariff.import.log",
            "view_mode": "list,form",
            "domain": [("provider_id", "=", self.id)],
            "target": "current",
            "context": dict(self.env.context),
        }

    def action_configure_mapping_from_csv(self):
        """Open wizard to configure mapping from a CSV file."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Configurer le mapping depuis CSV"),
            "res_model": "ftp.mapping.config.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_provider_id": self.id,
                "default_state": "upload",
            },
        }

    def action_browse_gdrive_folders(self):
        """Open wizard to browse Google Drive folders."""
        self.ensure_one()
        if self.protocol != "gdrive":
            raise UserError(_("Cette action est disponible uniquement pour les providers Google Drive."))
        if not self.gdrive_connected:
            raise UserError(_("Veuillez d'abord autoriser Google Drive."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Parcourir Google Drive"),
            "res_model": "ftp.gdrive.browser.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_provider_id": self.id,
                "default_current_folder_id": self.gdrive_folder_id or "root",
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        # Ensure default port matches protocol at creation when not provided
        new_vals_list = []
        for vals in vals_list:
            v = dict(vals or {})
            proto = (v.get("protocol") or "sftp")
            port_val = v.get("port")
            if not port_val:
                if proto == "imap":
                    imap_ssl = v.get("imap_use_ssl")
                    if imap_ssl is None:
                        imap_ssl = True
                    v["port"] = 993 if imap_ssl else 143
                elif proto == "ftp":
                    v["port"] = 21
                elif proto == "sftp":
                    v["port"] = 22
            new_vals_list.append(v)
        records = super().create(new_vals_list)
        # Auto create vendor if not provided and sync schedule if configured (batch-aware)
        schedule_keys = {"schedule_active", "schedule_interval_type", "schedule_interval_number", "schedule_time", "schedule_timezone"}
        schedule_pim_keys = {"schedule_pim_active", "schedule_pim_level", "schedule_pim_interval_type", "schedule_pim_interval_number", "schedule_pim_time", "schedule_pim_timezone"}
        for rec, vals in zip(records, vals_list):
            if not vals.get("partner_id"):
                Partner = self.env["res.partner"].sudo()
                partner_vals = {
                    "name": rec.name,
                    "company_id": rec.company_id.id,
                    "is_company": True,
                }
                if "supplier_rank" in Partner._fields:
                    partner_vals["supplier_rank"] = 1
                # autopost_bills is a Selection field (not boolean)
                # Values: 'never', 'always', etc. Default to 'never' to prevent auto-posting
                if "autopost_bills" in Partner._fields:
                    partner_vals["autopost_bills"] = "never"
                
                partner = Partner.create(partner_vals)
                rec.partner_id = partner.id
            # Sync cron if schedule configured
            try:
                if getattr(rec, "schedule_active", False) or schedule_keys.intersection(set(vals.keys())):
                    rec._sync_cron()
            except Exception:
                _logger.exception("Failed to sync cron on create for provider %s", rec.id)
            # Sync PIM cron if configured or relevant fields provided
            try:
                if getattr(rec, "schedule_pim_active", False) or schedule_pim_keys.intersection(set(vals.keys())):
                    rec._sync_pim_cron()
            except Exception:
                _logger.exception("Failed to sync PIM cron on create for provider %s", rec.id)
        return records

    def write(self, vals):
        res = super().write(vals)
        # Skip cron sync if context asks to
        if self.env.context.get("skip_schedule_sync"):
            return res
        # Keep partner name in sync if provider renamed
        if "name" in vals:
            for rec in self:
                if rec.partner_id:
                    try:
                        rec.partner_id.name = rec.name
                    except Exception:
                        _logger.warning("Could not sync partner name for provider %s", rec.id)
        # Sync cron when schedule fields changed
        # Sync cron when schedule fields changed
        schedule_keys = {"schedule_active", "schedule_interval_type", "schedule_interval_number", "schedule_time", "schedule_timezone"}
        if schedule_keys.intersection(set(vals.keys())):
            for rec in self:
                try:
                    rec._sync_cron()
                except Exception:
                    _logger.exception("Failed to sync cron on write for provider %s", rec.id)

        # Sync PIM cron when PIM schedule or time/interval fields changed
        # Sync PIM cron when PIM schedule or time/interval fields changed
        pim_keys = {"schedule_pim_active", "schedule_pim_level", "schedule_pim_interval_type", "schedule_pim_interval_number", "schedule_pim_time", "schedule_pim_timezone"}
        if pim_keys.intersection(set(vals.keys())):
            for rec in self:
                try:
                    rec._sync_pim_cron()
                except Exception:
                    _logger.exception("Failed to sync PIM cron on write for provider %s", rec.id)
        return res

    # ---------------------------
    # Scheduling helpers/actions
    # ---------------------------
    def action_apply_schedule(self):
        """Manually (re)synchronize the dedicated cron for this provider."""
        _logger.info(
            "FTP/provider: action_apply_schedule called by uid=%s on providers %s",
            self.env.uid,
            self.ids,
        )
        for rec in self:
            _logger.info(
                "FTP/provider: syncing FTP cron for provider id=%s name=%s "
                "active=%s interval_type=%s interval_number=%s time=%s tz=%s",
                rec.id,
                rec.display_name,
                rec.schedule_active,
                rec.schedule_interval_type,
                rec.schedule_interval_number,
                rec.schedule_time,
                rec.schedule_timezone,
            )
            rec._sync_cron()
        _logger.info(
            "FTP/provider: action_apply_schedule finished by uid=%s on providers %s",
            self.env.uid,
            self.ids,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Planification appliquée"),
                "message": _("La planification a été synchronisée."),
                "sticky": False,
            },
        }

    def action_apply_pim_schedule(self):
        """Manually (re)synchronize the dedicated PIM cron for this provider.

        This is intended to be a very light-weight action: it should not launch
        any PIM import, only (re)configure the dedicated PIM cron.
        """
        _logger.info(
            "PIM/provider_planning: action_apply_pim_schedule called by uid=%s on providers %s",
            self.env.uid,
            self.ids,
        )
        # Short-circuit: if no provider has PIM scheduling active and no dedicated
        # PIM cron, there is nothing to do. This avoids any write when the
        # button is clicked "for nothing" and keeps the UI reaction instant.
        providers_to_sync = self.filtered(
            lambda p: p.schedule_pim_active or p.schedule_pim_cron_id
        )
        if not providers_to_sync:
            _logger.info(
                "PIM/provider_planning: nothing to sync for providers %s (no active PIM schedule and no PIM cron)",
                self.ids,
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Planification PIM non modifiée"),
                    "message": _("Aucune planification PIM active à synchroniser."),
                    "sticky": False,
                },
            }

        for idx, rec in enumerate(providers_to_sync, start=1):
            _logger.info(
                "PIM/provider_planning: syncing PIM cron for provider id=%s name=%s "
                "active=%s level=%s interval_type=%s interval_number=%s time=%s tz=%s",
                rec.id,
                rec.display_name,
                rec.schedule_pim_active,
                rec.schedule_pim_level,
                rec.schedule_pim_interval_type,
                rec.schedule_pim_interval_number,
                rec.schedule_pim_time,
                rec.schedule_pim_timezone,
            )
            rec._sync_pim_cron()
            # Flush/commit per iteration to shorten lock windows and make the RPC return faster
            try:
                self.env.cr.flush()
                self.env.cr.commit()
            except Exception:
                _logger.debug(
                    "PIM/provider_planning: flush/commit failed (non-blocking) for provider %s",
                    rec.id,
                )
        _logger.info(
            "PIM/provider_planning: action_apply_pim_schedule finished by uid=%s on providers %s",
            self.env.uid,
            providers_to_sync.ids,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Planification PIM appliquée"),
                "message": _("La planification PIM a été synchronisée."),
                "sticky": False,
            },
        }

    def _sync_cron(self):
        """Ensure cron exists and is correctly configured, or disable it."""
        self.ensure_one()
        if self.schedule_active:
            self._ensure_cron_for_provider()
        else:
            if self.schedule_cron_id:
                try:
                    if self.schedule_cron_id.active:
                        self.schedule_cron_id.sudo().write({"active": False})
                except Exception:
                    _logger.exception("Failed to disable cron for provider %s", self.id)
        return True

    def _sync_pim_cron(self):
        """Ensure PIM cron exists and is correctly configured, or disable it."""
        self.ensure_one()
        if self.schedule_pim_active:
            self._ensure_pim_cron_for_provider()
        else:
            if self.schedule_pim_cron_id:
                try:
                    if self.schedule_pim_cron_id.active:
                        self.schedule_pim_cron_id.sudo().write({"active": False})
                except Exception:
                    _logger.exception("Failed to disable PIM cron for provider %s", self.id)
        return True

    def _get_schedule_nextcall_dt(self, prefix="schedule"):
        """Compute nextcall in UTC based on schedule_time and interval.
        prefix: 'schedule' or 'schedule_pim'
        """
        self.ensure_one()
        # Determine timezone; prefer explicit provider timezone, then user tz, else UTC
        tz = None
        try:
            tzname = (getattr(self, f"{prefix}_timezone") or self.env.user.tz or "UTC")
            if pytz and tzname:
                tz = pytz.timezone(tzname)
        except Exception:
            tz = None

        # Current time
        now_utc = datetime.utcnow()
        if tz:
            now_local = pytz.utc.localize(now_utc).astimezone(tz)
        else:
            now_local = now_utc

        # Scheduled local time today
        stime = float(getattr(self, f"{prefix}_time") or 0.0)
        hour = int(stime)
        minute = int(round((stime - hour) * 60.0))
        if minute >= 60:
            minute = 0
            hour = (hour + 1) % 24
        local_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If in the past, move forward by one interval
        if local_dt <= now_local:
            itype = getattr(self, f"{prefix}_interval_type") or "days"
            inumber = int(getattr(self, f"{prefix}_interval_number") or 1)
            if itype == "days":
                local_dt = local_dt + timedelta(days=inumber)
            else:
                local_dt = local_dt + timedelta(hours=inumber)

        # Convert back to UTC (naive)
        if tz:
            next_utc = local_dt.astimezone(pytz.UTC).replace(tzinfo=None)
        else:
            next_utc = local_dt  # already naive UTC approximation
        return fields.Datetime.to_string(next_utc)

    def _ensure_cron_for_provider(self):
        """Create or update the dedicated ir.cron entry for this provider."""
        self.ensure_one()
        Cron = self.env["ir.cron"].sudo()
        try:
            importer_model = self.env.ref("ftp_tariff_import.model_ftp_tariff_importer")
        except Exception:
            # Fallback: search by model name
            importer_model = self.env["ir.model"].sudo().search([("model", "=", "ftp.tariff.importer")], limit=1)
        try:
            user_root = self.env.ref("base.user_root")
        except Exception:
            user_root = self.env.user

        code = "model.process_provider(model.env['ftp.provider'].browse(%d))" % self.id
        target_vals = {
            "name": "[FTP] %s" % (self.name or ""),
            "active": bool(self.schedule_active),
            "model_id": importer_model.id if importer_model else False,
            "state": "code",
            "code": code,
            "interval_type": (self.schedule_interval_type or "days"),
            "interval_number": int(self.schedule_interval_number or 1),
            "user_id": user_root.id if user_root else False,
            "nextcall": self._get_schedule_nextcall_dt(prefix="schedule"),
        }
        if self.schedule_cron_id:
            cron = self.schedule_cron_id
            # Only write changed fields to reduce lock contention
            current = cron.read(["name", "active", "model_id", "state", "code", "interval_type", "interval_number", "user_id", "nextcall"])[0]
            if isinstance(current.get("model_id"), (list, tuple)):
                current["model_id"] = current["model_id"][0] if current["model_id"] else False
            if isinstance(current.get("user_id"), (list, tuple)):
                current["user_id"] = current["user_id"][0] if current["user_id"] else False
            diff = {k: v for k, v in target_vals.items() if current.get(k) != v}
            if diff:
                cron.write(diff)
                _logger.debug("Updated %s fields on cron %s for provider %s", list(diff.keys()), cron.id, self.id)
            else:
                _logger.debug("Cron already up-to-date for provider %s (no write)", self.id)
        else:
            cron = Cron.create(target_vals)
            # Assign M2O via context to avoid triggering schedule sync
            self.with_context(skip_schedule_sync=True).write({"schedule_cron_id": cron.id})
        return cron

    def _ensure_pim_cron_for_provider(self):
        """Create or update the dedicated PIM ir.cron entry for this provider."""
        self.ensure_one()
        Cron = self.env["ir.cron"].sudo()
        # Resolve model for planete.pim.importer
        importer_model = self.env["ir.model"].sudo().search([("model", "=", "planete.pim.importer")], limit=1)
        try:
            user_root = self.env.ref("base.user_root")
        except Exception:
            user_root = self.env.user

        code = "model.process_provider(model.env['ftp.provider'].browse(%d))" % self.id
        target_vals = {
            "name": "[PIM] %s" % (self.name or ""),
            "active": bool(self.schedule_pim_active),
            "model_id": importer_model.id if importer_model else False,
            "state": "code",
            "code": code,
            "interval_type": (self.schedule_pim_interval_type or "days"),
            "interval_number": int(self.schedule_pim_interval_number or 1),
            "user_id": user_root.id if user_root else False,
            "nextcall": self._get_schedule_nextcall_dt(prefix="schedule_pim"),
        }
        if self.schedule_pim_cron_id:
            cron = self.schedule_pim_cron_id
            # Only write changed fields to reduce lock contention
            current = cron.read(["name", "active", "model_id", "state", "code", "interval_type", "interval_number", "user_id", "nextcall"])[0]
            if isinstance(current.get("model_id"), (list, tuple)):
                current["model_id"] = current["model_id"][0] if current["model_id"] else False
            if isinstance(current.get("user_id"), (list, tuple)):
                current["user_id"] = current["user_id"][0] if current["user_id"] else False
            diff = {k: v for k, v in target_vals.items() if current.get(k) != v}
            if diff:
                cron.write(diff)
                _logger.debug("Updated %s fields on PIM cron %s for provider %s", list(diff.keys()), cron.id, self.id)
            else:
                _logger.debug("PIM cron already up-to-date for provider %s (no write)", self.id)
        else:
            cron = Cron.create(target_vals)
            # Assign M2O via context to avoid triggering schedule sync
            self.with_context(skip_schedule_sync=True).write({"schedule_pim_cron_id": cron.id})
        return cron

    # Helper accessors
    def get_barcode_candidates(self):
        self.ensure_one()
        return [c.strip() for c in (self.barcode_columns or "").split(",") if c.strip()]

    def get_price_column(self):
        self.ensure_one()
        return (self.price_column or "Prix de vente").strip()

    def get_csv_reader_params(self):
        self.ensure_one()
        delimiter = (self.csv_delimiter or ";")
        if len(delimiter) > 5:
            raise UserError(_("CSV delimiter can contain at most 5 characters."))
        enc = (self.csv_encoding or "auto").strip()
        encoding = "utf-8" if enc == "auto" else enc
        delimiter_regex = getattr(self, "pim_delimiter_regex", None)
        return {
            "delimiter": delimiter,
            "delimiter_regex": delimiter_regex,
            "has_header": bool(self.csv_has_header),
            "decimal_separator": (self.decimal_separator or "."),
            "encoding": encoding,
            "max_preview_rows": self.max_preview_rows or 200,
        }
