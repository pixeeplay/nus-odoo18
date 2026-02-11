# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class PlanetePimPlanRun(models.Model):
    _name = "planete.pim.plan.run"
    _description = "Planète PIM - Exécution planifiée"

    name = fields.Char(string="Nom", required=True, default=lambda self: _("Exécution %s") % fields.Datetime.now())
    provider_id = fields.Many2one("ftp.provider", string="Fournisseur (Provider)", required=True, ondelete="cascade")
    started_at = fields.Datetime(string="Début")
    ended_at = fields.Datetime(string="Fin")
    status = fields.Selection(
        [("pending", "En attente"), ("running", "En cours"), ("ok", "Succès"), ("failed", "Échec")],
        default="pending",
        string="Statut",
    )
    files_downloaded = fields.Integer(string="Fichiers téléchargés", default=0)
    files_imported = fields.Integer(string="Fichiers importés", default=0)
    log_html = fields.Html(string="Logs")
    last_error = fields.Text(string="Dernière erreur")
    attachment_ids = fields.One2many("planete.pim.plan.attachment", "run_id", string="Fichiers")
    duration_seconds = fields.Float(string="Durée (s)", compute="_compute_duration", store=False)

    @api.depends("started_at", "ended_at")
    def _compute_duration(self):
        for rec in self:
            if rec.started_at and rec.ended_at:
                dt_start = fields.Datetime.from_string(rec.started_at)
                dt_end = fields.Datetime.from_string(rec.ended_at)
                rec.duration_seconds = max(0.0, (dt_end - dt_start).total_seconds())
            else:
                rec.duration_seconds = 0.0

    def _append_log(self, html_chunk):
        for rec in self:
            try:
                rec.log_html = (rec.log_html or "") + (html_chunk or "")
            except Exception:
                pass

    def _execute_run(self):
        """Internal: execute this PIM plan run (heavy work, called by cron)."""
        self.ensure_one()
        rec = self

        if not rec.provider_id:
            raise UserError(_("Aucun fournisseur lié."))

        _logger.info(
            "PIM/planning: _execute_run starting for run id=%s, provider_id=%s, provider_name=%s, level=%s",
            rec.id,
            rec.provider_id.id,
            rec.provider_id.display_name,
            getattr(rec.provider_id, "schedule_pim_level", None),
        )

        try:
            rec.status = "running"
            if not rec.started_at:
                rec.started_at = fields.Datetime.now()

            importer = self.env["planete.pim.importer"].sudo()
            results = importer.process_provider(
                rec.provider_id,
                mode=getattr(rec.provider_id, "schedule_pim_level", None),
            )
            _logger.info(
                "PIM/planning: importer.process_provider returned %s result(s) for run id=%s, provider_id=%s",
                len(results) if results else 0,
                rec.id,
                rec.provider_id.id,
            )

            if results:
                ok = sum(1 for r in results if r.get("status") == "ok")
                ko = sum(1 for r in results if r.get("status") != "ok")
                rec._append_log(_("<h5>Résultats backend</h5><p>OK: %d | Erreurs: %d</p>") % (ok, ko))

            History = self.env["planete.pim.import.history"].sudo()
            Attachment = self.env["ir.attachment"].sudo()
            hist_domain = [("provider_id", "=", rec.provider_id.id)]
            if rec.started_at:
                hist_domain.append(("create_date", ">=", rec.started_at))
            if not rec.ended_at:
                hist_domain.append(("create_date", "<=", fields.Datetime.now()))
            histories = History.search(hist_domain, order="id asc")
            _logger.info(
                "PIM/planning: found %s history record(s) for run id=%s, provider_id=%s",
                len(histories),
                rec.id,
                rec.provider_id.id,
            )

            files_downloaded = 0
            files_ready = 0

            for h in histories:
                att_vals_proc = {
                    "name": (h.file_name or _("Fichier traité")),
                    "run_id": rec.id,
                    "kind": "processed",
                    "state": "ready",
                    "mimetype": "text/csv",
                    "log_id": h.log_id.id if h.log_id else False,
                    "size": 0,
                }
                self.env["planete.pim.plan.attachment"].sudo().create(att_vals_proc)
                files_ready += 1

                log_rec = h.log_id
                file_b64 = getattr(log_rec, "file_data", False)
                file_name = getattr(log_rec, "file_data_name", None) or (h.file_name or "source.csv")
                if file_b64:
                    try:
                        def _do_create():
                            return Attachment.create({
                                "name": file_name,
                                "datas": file_b64,
                                "res_model": "planete.pim.plan.run",
                                "res_id": rec.id,
                                "type": "binary",
                                "mimetype": "text/csv",
                            })
                        # Reuse importer helper with retry/backoff to mitigate lock timeouts on ir_attachment
                        ir_att = self.env["planete.pim.importer"].sudo()._with_lock_retry(_do_create, log=log_rec, swallow=True)
                        if ir_att:
                            self.env["planete.pim.plan.attachment"].sudo().create({
                                "name": file_name,
                                "run_id": rec.id,
                                "kind": "raw",
                                "state": "downloaded",
                                "mimetype": "text/csv",
                                "attachment_id": ir_att.id,
                                "log_id": log_rec.id if log_rec else False,
                                "size": getattr(ir_att, "file_size", 0) or 0,
                            })
                            files_downloaded += 1
                        else:
                            # Fallback without ir.attachment (do not fail the run)
                            self.env["planete.pim.plan.attachment"].sudo().create({
                                "name": file_name,
                                "run_id": rec.id,
                                "kind": "raw",
                                "state": "downloaded",
                                "mimetype": "text/csv",
                                "attachment_id": False,
                                "log_id": log_rec.id if log_rec else False,
                                "size": 0,
                            })
                            rec._append_log(_("<p>Notice: pièce jointe brute non créée (verrou concurrent sur ir_attachment). Import OK.</p>"))
                    except Exception:
                        # Any exception here should not fail the run; create a placeholder entry and log
                        self.env["planete.pim.plan.attachment"].sudo().create({
                            "name": file_name,
                            "run_id": rec.id,
                            "kind": "raw",
                            "state": "downloaded",
                            "mimetype": "text/csv",
                            "attachment_id": False,
                            "log_id": log_rec.id if log_rec else False,
                            "size": 0,
                        })
                        rec._append_log(_("<p>Notice: pièce jointe brute non créée (exception). Import OK.</p>"))

            rec.files_downloaded = files_downloaded
            rec.files_imported = files_ready
            rec.ended_at = fields.Datetime.now()
            rec.status = "ok"
            rec._append_log(_("<p>Téléchargés: %d — Traités (prêts): %d</p>") % (files_downloaded, files_ready))
            _logger.info(
                "PIM/planning: _execute_run finished OK for run id=%s, provider_id=%s "
                "(files_downloaded=%s, files_ready=%s)",
                rec.id,
                rec.provider_id.id,
                files_downloaded,
                files_ready,
            )
        except Exception as e:
            _logger.exception(
                "PIM/planning: _execute_run FAILED for run id=%s, provider_id=%s: %s",
                rec.id,
                rec.provider_id.id if rec.provider_id else None,
                e,
            )
            rec.ended_at = fields.Datetime.now()
            rec.status = "failed"
            rec.last_error = str(e)

    @api.model
    def _cron_process_pending_runs(self, limit=5):
        """Cron job: process pending PIM plan runs in background."""
        # Respect global kill switch if defined
        if self.env["ir.config_parameter"].sudo().get_param("planete_pim.disable_crons"):
            _logger.info("PIM/planning: _cron_process_pending_runs skipped because planete_pim.disable_crons is set")
            return

        runs = self.search([("status", "=", "pending")], order="id asc", limit=limit)
        if not runs:
            return

        _logger.info(
            "PIM/planning: _cron_process_pending_runs picked %s pending run(s): %s",
            len(runs),
            runs.ids,
        )

        ok_runs = 0
        failed_runs = 0

        for run in runs:
            try:
                run._execute_run()
                if run.status == "ok":
                    ok_runs += 1
                else:
                    failed_runs += 1
            except Exception:
                # _execute_run already logged and updated status/last_error
                failed_runs += 1
                continue

        _logger.info(
            "PIM/planning: _cron_process_pending_runs finished: ok=%s failed=%s (runs=%s)",
            ok_runs,
            failed_runs,
            runs.ids,
        )

    def action_execute_now(self):
        """Exécuter immédiatement (synchrone) l'exécution PIM pour chaque enregistrement sélectionné."""
        _logger.info(
            "PIM/planning: action_execute_now SYNCHRONOUS for runs %s by uid=%s",
            self.ids,
            self.env.uid,
        )
        for rec in self:
            if not rec.provider_id:
                raise UserError(_("Aucun fournisseur lié."))
            # Exécuter immédiatement et mettre à jour le statut/logs sur le même enregistrement
            rec._execute_run()

        # Retourner l'action de la liste/form des exécutions pour visualiser le résultat
        return {
            "type": "ir.actions.act_window",
            "name": _("Exécutions (Planification)"),
            "res_model": "planete.pim.plan.run",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [("id", "in", self.ids)],
        }


class PlanetePimPlanAttachment(models.Model):
    _name = "planete.pim.plan.attachment"
    _description = "Planète PIM - Fichier planification"

    name = fields.Char(required=True)
    run_id = fields.Many2one("planete.pim.plan.run", string="Exécution", required=True, ondelete="cascade")
    attachment_id = fields.Many2one("ir.attachment", string="Pièce jointe", ondelete="set null")
    size = fields.Integer(string="Taille (octets)")
    mimetype = fields.Char(string="Type MIME")
    kind = fields.Selection([("raw", "Brut"), ("processed", "Traité")], default="raw", string="Type")
    state = fields.Selection(
        [("downloaded", "Téléchargé"), ("ready", "Prêt à importer"), ("imported", "Importé"), ("error", "Erreur")],
        default="downloaded",
        string="État",
    )
    log_id = fields.Many2one("ftp.tariff.import.log", string="Journal d'import")
    provider_id = fields.Many2one(related="run_id.provider_id", store=False, readonly=True)

    def action_import_to_catalog(self):
        """Importer ce fichier dans le catalogue (utilise import_from_binary existant)."""
        self.ensure_one()
        b64 = None
        filename = self.name or "file.csv"
        if self.attachment_id and self.attachment_id.datas:
            b64 = self.attachment_id.datas
            if self.attachment_id.name:
                filename = self.attachment_id.name
        elif self.log_id and getattr(self.log_id, "file_data", False):
            b64 = self.log_id.file_data
            filename = getattr(self.log_id, "file_data_name", None) or filename

        if not b64:
            raise UserError(_("Aucune donnée binaire disponible pour cet élément."))

        try:
            supplier_id = None
            try:
                supplier_id = self.provider_id.partner_id.id if self.provider_id and self.provider_id.partner_id else None
            except Exception:
                supplier_id = None
            options = {
                "has_header": True,
                "encoding": None,
                "delimiter": None,
                "provider_id": self.provider_id.id if self.provider_id else None,
                "supplier_id": supplier_id,
                "do_write": True,
            }
            action = self.env["planete.pim.importer"].sudo().import_from_binary(b64, filename, options=options)
            if isinstance(action, dict) and action.get("res_model") == "ftp.tariff.import.log":
                self.log_id = action.get("res_id")
            self.state = "imported"
            return action or False
        except Exception as e:
            _logger.exception("Planification: import manuel échoué: %s", e)
            self.state = "error"
            raise
