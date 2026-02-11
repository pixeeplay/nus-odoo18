# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    x_last_import_log_id = fields.Many2one(
        "ftp.tariff.import.log",
        string="Dernier journal d'import",
        readonly=True,
        help="Journal de l'import associé au dernier traitement PIM.",
    )
    x_last_import_date = fields.Datetime(
        string="Dernière date d'import",
        readonly=True,
        help="Date/heure du dernier import PIM appliqué sur ce produit.",
    )
    x_origin_supplier_id = fields.Many2one(
        "res.partner",
        string="Fournisseur d'origine",
        help="Fournisseur source de l'import (peut changer à chaque import).",
    )
    x_created_by_supplier_id = fields.Many2one(
        "res.partner",
        string="Créé par (Fournisseur)",
        readonly=True,
        copy=False,
        help="Fournisseur qui a créé ce produit lors du premier import. Ne change jamais.",
    )
    x_last_digital_date = fields.Datetime(
        string="Dernière présence Digital",
        readonly=True,
        help="Date de la dernière fois que ce produit a été vu dans un fichier Digital/GroupeDigital.",
    )
    x_tech_specs = fields.Json(
        string="Caractéristiques techniques (JSON)",
        help="Spécifications techniques au format JSON.",
    )
    
    # DEEE Amount (Eco-contribution)
    deee_amount = fields.Float(
        string="DEEE (Eco-contribution)",
        digits='Product Price',
        help="Montant de l'éco-contribution DEEE (Déchets d'Équipements Électriques et Électroniques).",
    )

    @api.model
    def set_import_metadata(self, tmpl_ids, log_id=None, supplier_id=None, dt=None):
        """Helper to set import metadata in batch on templates."""
        if not tmpl_ids:
            return
        vals = {}
        if log_id:
            vals["x_last_import_log_id"] = log_id
        if supplier_id:
            vals["x_origin_supplier_id"] = supplier_id
        vals["x_last_import_date"] = dt or fields.Datetime.now()
        self.browse(tmpl_ids).write(vals)
