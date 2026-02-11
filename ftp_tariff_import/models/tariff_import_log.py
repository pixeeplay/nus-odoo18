# -*- coding: utf-8 -*-
from odoo import api, fields, models


class FtpTariffImportLog(models.Model):
    _name = "ftp.tariff.import.log"
    _description = "FTP/SFTP/IMAP Tariff Import Log"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: self._default_name())
    provider_id = fields.Many2one("ftp.provider", string="Provider", ondelete="set null")
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company.id,
    )
    protocol = fields.Selection(
        selection=[("ftp", "FTP"), ("sftp", "SFTP"), ("imap", "IMAP")],
        readonly=True,
    )
    file_name = fields.Char(readonly=True)
    state = fields.Selection(
        selection=[("pending", "Pending"), ("done", "Done"), ("error", "Error")],
        default="pending",
        index=True,
    )
    started_at = fields.Datetime(readonly=True)
    ended_at = fields.Datetime(readonly=True)
    duration_sec = fields.Float(readonly=True)
    total_lines = fields.Integer(readonly=True)
    success_count = fields.Integer(readonly=True)
    error_count = fields.Integer(readonly=True)
    log_html = fields.Html(sanitize=False)
    message = fields.Text(help="Short message/summary")
    file_data = fields.Binary("Source File", attachment=True, readonly=True)
    file_data_name = fields.Char("Source File Name", readonly=True)
    is_mapping = fields.Boolean(string="Mapping Import", default=False, readonly=True)
    remote_mtime = fields.Datetime(string="Remote Modified At", readonly=True)
    provider_last_connection_status = fields.Selection(
        selection=[("ok", "OK"), ("failed", "Failed")],
        string="Provider Last Connection",
        readonly=True,
    )
    provider_last_run_at = fields.Datetime(string="Provider Last Run", readonly=True)

    @api.model
    def _default_name(self):
        return self.env["ir.sequence"].next_by_code("ftp.tariff.import.log") or "Import Log"

    def mark_started(self):
        for rec in self:
            rec.write({
                "started_at": fields.Datetime.now(),
                "state": "pending",
            })

    def mark_done(self, total=0, success=0, error=0, msg=None):
        for rec in self:
            values = {
                "ended_at": fields.Datetime.now(),
                "state": "done",
                "total_lines": total,
                "success_count": success,
                "error_count": error,
            }
            if rec.started_at:
                delta = fields.Datetime.now() - rec.started_at
                values["duration_sec"] = delta.total_seconds()
            if msg:
                values["message"] = msg
            rec.write(values)

    def mark_error(self, msg=None):
        for rec in self:
            values = {
                "ended_at": fields.Datetime.now(),
                "state": "error",
            }
            if rec.started_at:
                delta = fields.Datetime.now() - rec.started_at
                values["duration_sec"] = delta.total_seconds()
            if msg:
                values["message"] = msg
            rec.write(values)
