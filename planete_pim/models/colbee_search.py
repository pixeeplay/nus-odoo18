# -*- coding: utf-8 -*-
"""
Colbee Search - Recherche multi-critères produit avec vue fournisseurs
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging
_logger = logging.getLogger(__name__)


class ColbeeSearchWizard(models.TransientModel):
    _name = "colbee.search.wizard"
    _description = "Colbee - Recherche produit multi-critères"

    search_term = fields.Char(
        string="Recherche",
        help="Saisissez un code-barres, une référence, un nom de produit ou un EAN",
    )
    product_id = fields.Many2one(
        "product.template", string="Produit trouvé", readonly=True)
    product_name = fields.Char(
        related="product_id.name", string="Nom du produit")
    product_barcode = fields.Char(
        related="product_id.barcode", string="Code-barres")
    product_default_code = fields.Char(
        related="product_id.default_code", string="Référence interne")
    product_brand = fields.Char(
        string="Marque", compute="_compute_product_brand")
    product_list_price = fields.Float(
        related="product_id.list_price", string="Prix de vente")
    product_standard_price = fields.Float(
        related="product_id.standard_price", string="Coût")
    product_qty_available = fields.Float(
        related="product_id.qty_available", string="Stock disponible")
    search_performed = fields.Boolean(
        string="Recherche effectuée", default=False)
    no_result = fields.Boolean(string="Aucun résultat", default=False)
    supplier_line_ids = fields.One2many(
        "colbee.supplier.line", "wizard_id", string="Fournisseurs")

    @api.depends("product_id")
    def _compute_product_brand(self):
        for rec in self:
            brand = ""
            if rec.product_id:
                # Essayer différents noms de champs pour la marque
                if hasattr(rec.product_id, 'product_brand_id') and rec.product_id.product_brand_id:
                    brand = rec.product_id.product_brand_id.name
                elif hasattr(rec.product_id, 'brand_id') and rec.product_id.brand_id:
                    brand = rec.product_id.brand_id.name
            rec.product_brand = brand

    def action_search(self):
        """Effectue la recherche multi-critères."""
        self.ensure_one()

        if not self.search_term:
            raise UserError(_("Veuillez saisir un terme de recherche"))

        search_term = self.search_term.strip()
        ProductTemplate = self.env["product.template"].sudo()
        ProductProduct = self.env["product.product"].sudo()

        product = None

        # 1. Recherche par code-barres exact
        if not product:
            variant = ProductProduct.search([("barcode", "=", search_term)], limit=1)
            if variant:
                product = variant.product_tmpl_id

        # 2. Recherche par référence exacte
        if not product:
            variant = ProductProduct.search([("default_code", "=", search_term)], limit=1)
            if variant:
                product = variant.product_tmpl_id

        # 3. Recherche par référence sur template
        if not product:
            product = ProductTemplate.search([("default_code", "=", search_term)], limit=1)

        # 4. Recherche par code-barres partiel (contient)
        if not product:
            variant = ProductProduct.search([("barcode", "ilike", search_term)], limit=1)
            if variant:
                product = variant.product_tmpl_id

        # 5. Recherche par référence partielle
        if not product:
            variant = ProductProduct.search([("default_code", "ilike", search_term)], limit=1)
            if variant:
                product = variant.product_tmpl_id

        # 6. Recherche par nom
        if not product:
            product = ProductTemplate.search([("name", "ilike", search_term)], limit=1)

        # Mettre à jour les résultats
        if product:
            self.product_id = product.id
            self.search_performed = True
            self.no_result = False

            # Charger les lignes fournisseurs
            self._load_supplier_lines(product)
        else:
            self.product_id = False
            self.search_performed = True
            self.no_result = True
            # Supprimer les anciennes lignes
            self.supplier_line_ids.unlink()

        # Retourner sur le même wizard pour voir les résultats
        return {
            "type": "ir.actions.act_window",
            "res_model": "colbee.search.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def _load_supplier_lines(self, product):
        """Charge les lignes fournisseurs pour un produit."""
        # Supprimer les anciennes lignes
        self.supplier_line_ids.unlink()

        SupplierInfo = self.env["product.supplierinfo"].sudo()
        SupplierLine = self.env["colbee.supplier.line"].sudo()

        # Trouver tous les supplierinfo pour ce produit
        supplier_infos = SupplierInfo.search([
            ("product_tmpl_id", "=", product.id),
        ], order="write_date desc")

        for si in supplier_infos:
            # Récupérer le stock fournisseur si le champ existe
            supplier_stock = 0.0
            if "supplier_stock" in si._fields:
                supplier_stock = si.supplier_stock or 0.0

            SupplierLine.create({
                "wizard_id": self.id,
                "supplierinfo_id": si.id,
                "supplier_name": si.partner_id.name if si.partner_id else "",
                "supplier_id": si.partner_id.id if si.partner_id else False,
                "price": si.price,
                "supplier_stock": supplier_stock,
                "min_qty": si.min_qty,
                "delay": si.delay if hasattr(si, 'delay') else 0,
                "last_update": si.write_date,
                "product_code": si.product_code or "",
                "product_name": si.product_name or "",
            })

    def action_clear(self):
        """Efface la recherche et les résultats."""
        self.ensure_one()
        self.search_term = ""
        self.product_id = False
        self.search_performed = False
        self.no_result = False
        self.supplier_line_ids.unlink()

        return {
            "type": "ir.actions.act_window",
            "res_model": "colbee.search.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_product(self):
        """Ouvre le formulaire produit."""
        self.ensure_one()
        if not self.product_id:
            return

        return {
            "type": "ir.actions.act_window",
            "res_model": "product.template",
            "res_id": self.product_id.id,
            "view_mode": "form",
            "target": "current",
        }


class ColbeeSupplierLine(models.TransientModel):
    _name = "colbee.supplier.line"
    _description = "Colbee - Ligne fournisseur"
    _order = "last_update desc"

    wizard_id = fields.Many2one(
        "colbee.search.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )

    supplierinfo_id = fields.Many2one(
        "product.supplierinfo",
        string="SupplierInfo",
    )

    supplier_id = fields.Many2one(
        "res.partner",
        string="Fournisseur",
    )

    supplier_name = fields.Char(
        string="Nom du fournisseur",
    )

    price = fields.Float(
        string="Prix d'achat",
        digits="Product Price",
    )

    supplier_stock = fields.Float(
        string="Stock fournisseur",
    )

    min_qty = fields.Float(
        string="Qté min.",
    )

    delay = fields.Integer(
        string="Délai (jours)",
    )

    last_update = fields.Datetime(
        string="Dernière MAJ",
    )

    product_code = fields.Char(
        string="Réf. fournisseur",
    )

    product_name = fields.Char(
        string="Nom fournisseur",
    )

    def action_open_supplier(self):
        """Ouvre le formulaire du fournisseur."""
        self.ensure_one()
        if not self.supplier_id:
            return

        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": self.supplier_id.id,
            "view_mode": "form",
            "target": "current",
        }
