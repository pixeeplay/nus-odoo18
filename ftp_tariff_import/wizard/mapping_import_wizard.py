# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class MappingImportWizard(models.TransientModel):
    """Wizard pour importer un template de mapping depuis un fichier JSON."""
    _name = "ftp.mapping.import.wizard"
    _description = "Import de template de mapping JSON"

    file_data = fields.Binary(
        string="Fichier JSON",
        required=True,
        help="Fichier JSON exporté depuis un autre template de mapping",
    )
    file_name = fields.Char(string="Nom du fichier")
    provider_id = fields.Many2one(
        "ftp.provider",
        string="Fournisseur (optionnel)",
        help="Assigner ce fournisseur aux templates importés. Si vide, le fournisseur sera déduit du JSON.",
    )

    def action_import(self):
        """Lance l'import du fichier JSON."""
        self.ensure_one()
        
        if not self.file_data:
            raise UserError(_("Veuillez sélectionner un fichier JSON."))
        
        Template = self.env["ftp.mapping.template"]
        created_ids = Template.action_import_json(
            self.file_data,
            filename=self.file_name,
            provider_id=self.provider_id.id if self.provider_id else None,
        )
        
        if not created_ids:
            raise UserError(_("Aucun template n'a pu être importé depuis le fichier."))
        
        # Retourner la vue des templates créés
        if len(created_ids) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "ftp.mapping.template",
                "view_mode": "form",
                "res_id": created_ids[0],
            }
        else:
            return {
                "type": "ir.actions.act_window",
                "res_model": "ftp.mapping.template",
                "view_mode": "list,form",
                "domain": [("id", "in", created_ids)],
                "name": _("%d templates importés") % len(created_ids),
            }
