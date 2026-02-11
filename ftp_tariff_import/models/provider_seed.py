# -*- coding: utf-8 -*-
from odoo import api, models
import logging
import os
import json
import csv
import io
import re

_logger = logging.getLogger(__name__)


class FtpProviderSeed(models.Model):
    _inherit = "ftp.provider"

    @api.model
    def seed_from_path(self, path):
        """Create/update ftp.provider records from a local seed file.

        - Path is resolved from env var IVSPRO_FTP_PROVIDERS_PATH or ir.config_parameter
        - Supported formats:
            * JSON: object or list of objects
            * CSV: header-based; fallback 4 columns "host;login;mdp;nom"
            * Q/A text: blocks of lines "Key? Value" (also ":" or "="), blocks separated by blank line
            * Plain text single-line fallback "host;login;mdp;nom"
        - Idempotent by (name, company)
        - Does not overwrite password if not provided in the entry
        """
        path = os.path.expanduser(path or "")
        if not path or not os.path.exists(path):
            _logger.info("ftp_tariff_import: providers seed path does not exist: %s", path)
            return

        try:
            entries = self._parse_seed_file(path)
        except Exception:
            _logger.exception("ftp_tariff_import: failed to parse providers file: %s", path)
            return

        created = 0
        updated = 0
        for raw in entries:
            try:
                vals = self._normalize_entry(raw)
                name = (vals.get("name") or "").strip()
                host = (vals.get("host") or "").strip()
                if not name or not host:
                    continue

                company_id = vals.pop("company_id", None) or self.env.company.id
                vals["company_id"] = company_id
                partner_name = vals.pop("__partner_name__", None)

                # Upsert by (name, company)
                existing = self.search([("name", "=", name), ("company_id", "=", company_id)], limit=1)
                if existing:
                    write_vals = dict(vals)
                    # do not erase password if empty/missing
                    if "password" in write_vals and not write_vals["password"]:
                        write_vals.pop("password")
                    existing.write(write_vals)
                    rec = existing
                    updated += 1
                else:
                    rec = self.create(vals)
                    created += 1

                # Optional explicit partner link by name
                if partner_name:
                    partner = self.env["res.partner"].search(
                        [("name", "=", partner_name), ("company_id", "=", company_id)], limit=1
                    )
                    if not partner:
                        Partner = self.env["res.partner"].sudo()
                        partner_vals = {"name": partner_name, "company_id": company_id}
                        if "supplier_rank" in Partner._fields:
                            partner_vals["supplier_rank"] = 1
                        # Ensure autopost_bills is set if the field exists (required NOT NULL)
                        if "autopost_bills" in Partner._fields:
                            partner_vals["autopost_bills"] = False
                        partner = Partner.create(partner_vals)
                    rec.partner_id = partner.id
            except Exception:
                _logger.exception(
                    "ftp_tariff_import: failed to upsert provider from entry (name=%s)",
                    raw.get("name"),
                )

        _logger.info(
            "ftp_tariff_import: providers seed done from %s (created=%s, updated=%s)",
            path,
            created,
            updated,
        )

    # -----------------------------
    # Parsers
    # -----------------------------
    def _parse_seed_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            data = f.read()

        if ext == ".json":
            return self._parse_json(data)
        if ext in (".csv", ".tsv"):
            return self._parse_csv(data, delimiter="\t" if ext == ".tsv" else None)

        # Text: try Q/A blocks then semicolon fallback
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="ignore")

        blocks = self._parse_qa_text(text)
        if blocks:
            return blocks

        return self._parse_semicolon_line(text)

    def _parse_json(self, content_bytes):
        payload = json.loads(content_bytes.decode("utf-8"))
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return payload
        return []

    def _parse_csv(self, content_bytes, delimiter=None):
        # auto-detect delimiter if None
        sample = content_bytes[:2048].decode("utf-8", errors="ignore")
        if not delimiter:
            delimiter = ";" if sample.count(";") >= sample.count(",") else ","

        f = io.StringIO(content_bytes.decode("utf-8", errors="ignore"))
        reader = csv.DictReader(f, delimiter=delimiter)
        out = []
        if reader.fieldnames and len(reader.fieldnames) >= 2:
            for row in reader:
                out.append(row)
            return out

        # No header: fallback 4 columns "host;login;mdp;nom"
        f2 = io.StringIO(content_bytes.decode("utf-8", errors="ignore"))
        reader2 = csv.reader(f2, delimiter=delimiter)
        for r in reader2:
            if r and len(r) >= 4:
                out.append({"host": r[0], "login": r[1], "mdp": r[2], "nom": r[3]})
        return out

    def _parse_qa_text(self, text):
        # Blocks split by blank lines; lines of "Key? Value" or "Key: Value" or "Key = Value"
        blocks = re.split(r"\n\s*\n", text.strip(), flags=re.M)
        out = []
        for block in blocks:
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if not lines:
                continue
            entry = {}
            for ln in lines:
                if "?" in ln:
                    k, v = ln.split("?", 1)
                elif ":" in ln:
                    k, v = ln.split(":", 1)
                elif "=" in ln:
                    k, v = ln.split("=", 1)
                else:
                    # maybe "host;login;mdp;nom" single line
                    parts = [p.strip() for p in ln.split(";")]
                    if len(parts) >= 4:
                        entry.update({"host": parts[0], "login": parts[1], "mdp": parts[2], "nom": parts[3]})
                    continue
                entry[k.strip()] = v.strip()
            if entry:
                out.append(entry)
        return out

    def _parse_semicolon_line(self, text):
        for ln in (text or "").splitlines():
            parts = [p.strip() for p in ln.split(";")]
            if len(parts) >= 4 and parts[0] and parts[1]:
                return [{"host": parts[0], "login": parts[1], "mdp": parts[2], "nom": parts[3]}]
        return []

    # -----------------------------
    # Normalization/mapping
    # -----------------------------
    def _norm_key(self, k):
        k = (k or "").strip().lower()
        k = (
            k.replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("ë", "e")
            .replace("à", "a")
            .replace("â", "a")
            .replace("ä", "a")
            .replace("ô", "o")
            .replace("ö", "o")
            .replace("û", "u")
            .replace("ü", "u")
        )
        k = re.sub(r"[^a-z0-9]+", "_", k)
        k = re.sub(r"_+", "_", k)
        return k.strip("_")

    def _normalize_entry(self, entry):
        # Synonyms map (French/English)
        synonyms = {
            "name": {"name", "nom", "provider", "fournisseur_nom"},
            "company_name": {"company", "company_name", "societe", "societe_nom", "entreprise", "my_company"},
            "protocol": {"protocol", "protocole", "type"},
            "host": {"host", "hote", "hostname", "server"},
            "port": {"port"},
            "username": {"username", "login", "user", "utilisateur", "identifiant"},
            "password": {"password", "pass", "mdp", "mot_de_passe"},
            "remote_dir_in": {"remote_dir_in", "dir_in", "remote_in", "incoming", "dossier_entree", "repertoire_entree"},
            "remote_dir_processed": {
                "remote_dir_processed",
                "dir_processed",
                "processed",
                "repertoire_traite",
                "dossier_traite",
            },
            "remote_dir_error": {
                "remote_dir_error",
                "dir_error",
                "error",
                "errors",
                "repertoire_erreur",
                "dossier_erreur",
            },
            "file_pattern": {"file_pattern", "pattern", "glob", "mask"},
            "timeout": {"timeout"},
            "retries": {"retries", "retry", "essais"},
            "keepalive": {"keepalive"},
            "partner_name": {"partner", "fournisseur", "vendor"},
        }
        # IMAP-specific synonyms and mailbox aliases
        synonyms.update({
            "imap_use_ssl": {"imap_use_ssl", "imap_ssl", "use_ssl", "ssl", "tls", "starttls"},
            "imap_search_criteria": {"imap_search_criteria", "search", "search_criteria", "imap_search"},
            "imap_mark_seen": {"imap_mark_seen", "mark_seen", "seen"},
            "imap_move_processed": {"imap_move_processed", "move_processed", "move_success", "move_ok"},
            "imap_move_error": {"imap_move_error", "move_error", "move_fail", "move_ko"},
        })
        # Extend mailbox/folder synonyms for IMAP sources
        synonyms["remote_dir_in"] = set(synonyms.get("remote_dir_in", set())) | {
            "mailbox", "folder", "mailbox_in", "mailbox_source", "boite", "boite_reception", "inbox", "dossier", "repertoire"
        }
        synonyms["remote_dir_processed"] = set(synonyms.get("remote_dir_processed", set())) | {
            "processed_mailbox", "mailbox_processed", "boite_traitee", "archive", "archives", "traite"
        }
        synonyms["remote_dir_error"] = set(synonyms.get("remote_dir_error", set())) | {
            "error_mailbox", "mailbox_error", "boite_erreur", "erreur", "rejet", "rejets", "rejected", "failed", "echec"
        }
        inv = {}
        for canon, keys in synonyms.items():
            for k in keys:
                inv[k] = canon

        flat = {}
        for k, v in (entry or {}).items():
            nk = self._norm_key(k)
            canon = inv.get(nk) or nk
            flat[canon] = v

        vals = {}
        # name
        name = (flat.get("name") or flat.get("nom") or flat.get("provider") or flat.get("host") or "").strip()
        vals["name"] = name

        # company_id
        company_name = (flat.get("company_name") or "").strip()
        company = None
        if company_name:
            company = self.env["res.company"].sudo().search([("name", "=", company_name)], limit=1)
        vals["company_id"] = company.id if company else self.env.company.id

        # protocol
        prot = str(flat.get("protocol") or "").strip().lower()
        if prot in ("sftp", "ssh"):
            vals["protocol"] = "sftp"
        elif prot in ("ftp",):
            vals["protocol"] = "ftp"
        elif prot in ("imap", "imap4", "email", "mail"):
            vals["protocol"] = "imap"
        else:
            vals["protocol"] = "sftp"

        # host/port
        vals["host"] = str(flat.get("host") or "").strip()
        try:
            # Determine port: explicit value wins; otherwise infer from protocol (including IMAP SSL)
            if flat.get("port") not in (None, "", False):
                vals["port"] = int(flat.get("port"))
            else:
                if vals["protocol"] == "sftp":
                    vals["port"] = 22
                elif vals["protocol"] == "ftp":
                    vals["port"] = 21
                elif vals["protocol"] == "imap":
                    # parse imap_use_ssl (default True)
                    raw_ssl = flat.get("imap_use_ssl")
                    if isinstance(raw_ssl, bool):
                        imap_ssl = raw_ssl
                    else:
                        s = str(raw_ssl).strip().lower() if raw_ssl not in (None, "", False) else ""
                        imap_ssl = True if s == "" else s in ("1", "true", "t", "y", "yes", "on", "vrai", "oui", "o", "ok")
                    vals["port"] = 993 if imap_ssl else 143
                else:
                    vals["port"] = 0
        except Exception:
            vals["port"] = 22 if vals["protocol"] == "sftp" else (21 if vals["protocol"] == "ftp" else (993 if vals["protocol"] == "imap" else 0))

        # credentials
        if "username" in flat:
            vals["username"] = str(flat.get("username") or "").strip()
        if "password" in flat:
            vals["password"] = str(flat.get("password") or "").strip()

        # directories and pattern
        if "remote_dir_in" in flat:
            vals["remote_dir_in"] = (str(flat.get("remote_dir_in") or "/").strip() or "/")
        if "remote_dir_processed" in flat:
            vals["remote_dir_processed"] = (str(flat.get("remote_dir_processed") or "/processed").strip() or "/processed")
        if "remote_dir_error" in flat:
            vals["remote_dir_error"] = (str(flat.get("remote_dir_error") or "/error").strip() or "/error")
        if "file_pattern" in flat:
            vals["file_pattern"] = (str(flat.get("file_pattern") or "*").strip() or "*")
        # IMAP: set mailbox defaults if missing
        if vals.get("protocol") == "imap":
            if not vals.get("remote_dir_in"):
                vals["remote_dir_in"] = "INBOX"
            if not vals.get("remote_dir_processed"):
                vals["remote_dir_processed"] = "Processed"
            if not vals.get("remote_dir_error"):
                vals["remote_dir_error"] = "Error"
        # IMAP options
        if "imap_use_ssl" in flat:
            v = flat.get("imap_use_ssl")
            if isinstance(v, bool):
                vals["imap_use_ssl"] = v
            else:
                sv = str(v).strip().lower()
                if sv in ("1", "true", "t", "y", "yes", "on", "vrai", "oui", "o", "ok"):
                    vals["imap_use_ssl"] = True
                elif sv in ("0", "false", "f", "n", "no", "off", "faux", "non"):
                    vals["imap_use_ssl"] = False
        if "imap_search_criteria" in flat:
            vals["imap_search_criteria"] = str(flat.get("imap_search_criteria") or "").strip() or "ALL"
        if "imap_mark_seen" in flat:
            v = flat.get("imap_mark_seen")
            if isinstance(v, bool):
                vals["imap_mark_seen"] = v
            else:
                sv = str(v).strip().lower()
                if sv in ("1", "true", "t", "y", "yes", "on", "vrai", "oui", "o", "ok"):
                    vals["imap_mark_seen"] = True
                elif sv in ("0", "false", "f", "n", "no", "off", "faux", "non"):
                    vals["imap_mark_seen"] = False
        if "imap_move_processed" in flat:
            v = flat.get("imap_move_processed")
            if isinstance(v, bool):
                vals["imap_move_processed"] = v
            else:
                sv = str(v).strip().lower()
                if sv in ("1", "true", "t", "y", "yes", "on", "vrai", "oui", "o", "ok"):
                    vals["imap_move_processed"] = True
                elif sv in ("0", "false", "f", "n", "no", "off", "faux", "non"):
                    vals["imap_move_processed"] = False
        if "imap_move_error" in flat:
            v = flat.get("imap_move_error")
            if isinstance(v, bool):
                vals["imap_move_error"] = v
            else:
                sv = str(v).strip().lower()
                if sv in ("1", "true", "t", "y", "yes", "on", "vrai", "oui", "o", "ok"):
                    vals["imap_move_error"] = True
                elif sv in ("0", "false", "f", "n", "no", "off", "faux", "non"):
                    vals["imap_move_error"] = False

        # numeric options
        def _to_int(x, default=None):
            try:
                if x in (None, "", False):
                    return default
                return int(str(x).strip())
            except Exception:
                return default

        ti = _to_int(flat.get("timeout"))
        if ti is not None:
            vals["timeout"] = ti
        rets = _to_int(flat.get("retries"))
        if rets is not None:
            vals["retries"] = rets
        ka = _to_int(flat.get("keepalive"))
        if ka is not None:
            vals["keepalive"] = ka

        # explicit partner name
        partner_name = (flat.get("partner_name") or flat.get("fournisseur") or "").strip()
        if partner_name:
            vals["__partner_name__"] = partner_name

        return vals
