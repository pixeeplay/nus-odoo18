# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _


_logger = logging.getLogger(__name__)


class FtpProviderPlanning(models.Model):
    _inherit = "ftp.provider"

    last_log_id = fields.Many2one(
        "ftp.tariff.import.log",
        string="Dernier log",
        compute="_compute_last_log",
        store=False,
        readonly=True,
    )
    last_file_data_name = fields.Char(
        string="Dernier fichier",
        compute="_compute_last_log",
        store=False,
        readonly=True,
    )
    last_file_available = fields.Boolean(
        string="Fichier disponible",
        compute="_compute_last_log",
        store=False,
        readonly=True,
    )
    last_log_imported_at = fields.Datetime(
        string="Importé le",
        compute="_compute_last_log",
        store=False,
        readonly=True,
    )
    last_log_remote_mtime = fields.Datetime(
        string="Fichier modifié (remote)",
        compute="_compute_last_log",
        store=False,
        readonly=True,
    )
    # Next scheduled runs (from related crons)
    next_run_at = fields.Datetime(
        related="schedule_cron_id.nextcall",
        string="Prochaine exécution FTP",
        readonly=True,
    )
    pim_next_run_at = fields.Datetime(
        related="schedule_pim_cron_id.nextcall",
        string="Prochaine exécution PIM",
        readonly=True,
    )

    @api.depends("last_run_at")
    def _compute_last_log(self):
        Log = self.env["ftp.tariff.import.log"].sudo()
        for rec in self:
            log = Log.search([("provider_id", "=", rec.id)], limit=1, order="create_date desc, id desc")
            rec.last_log_id = log.id if log else False
            rec.last_file_data_name = log.file_data_name if log else False
            rec.last_file_available = bool(log and log.file_data)
            rec.last_log_imported_at = log.create_date if log else False
            # remote_mtime filled best-effort by importer when available
            rec.last_log_remote_mtime = log.remote_mtime if log else False

    def action_run_ftp_now(self):
        """Run FTP tariff import now for selected providers and update status."""
        _logger.info(
            "PIM/provider_planning: action_run_ftp_now called by uid=%s on providers %s",
            self.env.uid,
            self.ids,
        )
        ok = 0
        failed = 0
        for rec in self:
            now = fields.Datetime.now()
            _logger.info(
                "PIM/provider_planning: [FTP] starting process_provider for provider "
                "id=%s name=%s company_id=%s",
                rec.id,
                rec.display_name,
                rec.company_id.id,
            )
            try:
                self.env["ftp.tariff.importer"].with_company(rec.company_id).process_provider(rec)
                ok += 1
                _logger.info(
                    "PIM/provider_planning: [FTP] process_provider finished OK for provider id=%s",
                    rec.id,
                )
            except Exception as e:
                _logger.exception(
                    "PIM/provider_planning: [FTP] process_provider FAILED for provider id=%s: %s",
                    rec.id,
                    e,
                )
                # Ensure failure is reflected in provider status, without sudo spam.
                rec.write(
                    {
                        "last_connection_status": "failed",
                        "last_error": str(e),
                        "last_run_at": now,
                    }
                )
                failed += 1
        title = _("Import FTP lancé")
        msg = _("Succès: %d | Échecs: %d") % (ok, failed)
        _logger.info(
            "PIM/provider_planning: action_run_ftp_now finished by uid=%s: ok=%s failed=%s",
            self.env.uid,
            ok,
            failed,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "message": msg, "sticky": False},
        }

    def action_run_pim_now(self):
        """Queue PIM plan executions for selected providers (non-blocking)."""
        _logger.info(
            "PIM/provider_planning: action_run_pim_now QUEUEING by uid=%s on providers %s",
            self.env.uid,
            self.ids,
        )
        Run = self.env["planete.pim.plan.run"]
        created_runs = self.env["planete.pim.plan.run"]

        for rec in self:
            if not rec.id:
                continue
            run_vals = {
                "provider_id": rec.id,
                # name uses default on model
            }
            run = Run.create(run_vals)
            created_runs |= run
            _logger.info(
                "PIM/provider_planning: queued plan run id=%s for provider id=%s (%s)",
                run.id,
                rec.id,
                rec.display_name,
            )

        if not created_runs:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Planification PIM"),
                    "message": _("Aucune exécution n'a été planifiée."),
                    "sticky": False,
                    "type": "warning",
                },
            }

        _logger.info(
            "PIM/provider_planning: action_run_pim_now queued %s run(s): %s",
            len(created_runs),
            created_runs.ids,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Planification PIM"),
                "message": _(
                    "Les exécutions (%d) ont été mises en file d'attente. "
                    "Elles seront traitées par le serveur en arrière-plan."
                )
                % len(created_runs),
                "sticky": False,
                "type": "info",
            },
        }

    def action_open_last_log(self):
        """Open last log record for provider."""
        self.ensure_one()
        _logger.info(
            "PIM/provider_planning: action_open_last_log called for provider id=%s, last_log_id=%s",
            self.id,
            self.last_log_id.id if self.last_log_id else False,
        )
        if not self.last_log_id:
            _logger.info(
                "PIM/provider_planning: no last_log_id found for provider id=%s, returning notification",
                self.id,
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Aucun log"),
                    "message": _("Aucun journal trouvé pour ce fournisseur."),
                    "sticky": False,
                },
            }
        _logger.info(
            "PIM/provider_planning: opening last_log_id=%s for provider id=%s",
            self.last_log_id.id,
            self.id,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "ftp.tariff.import.log",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": self.last_log_id.id,
            "target": "current",
        }
