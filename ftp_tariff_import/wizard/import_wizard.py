# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FtpImportWizard(models.TransientModel):
    _name = "ftp.import.wizard"
    _description = "FTP/SFTP/IMAP Manual Import Wizard"

    provider_id = fields.Many2one("ftp.provider", string="Provider", required=True)
    only_list_pattern = fields.Boolean(
        string="Use Provider Pattern Only",
        default=True,
        help="If checked, process files matching provider pattern. Otherwise you can specify additional remote paths below."
    )
    extra_paths = fields.Text(
        string="Extra Remote Paths",
        help="Optional: one remote path per line to process in addition to provider pattern."
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("default_provider_id"):
            res["provider_id"] = self.env.context["default_provider_id"]
        return res

    def action_process_now(self):
        self.ensure_one()
        provider = self.provider_id.with_company(self.provider_id.company_id)
        importer = self.env["ftp.tariff.importer"].with_company(provider.company_id)

        if self.only_list_pattern:
            importer.process_provider(provider)
        else:
            paths = []
            if self.extra_paths:
                for line in (self.extra_paths or "").splitlines():
                    p = (line or "").strip()
                    if p:
                        paths.append(p)
            if not paths:
                raise UserError(_("Provide at least one remote path or enable 'Use Provider Pattern Only'."))
            importer.process_selected_files(provider, paths)

        provider.sudo().write({"last_run_at": fields.Datetime.now()})
        return {"type": "ir.actions.act_window_close"}
