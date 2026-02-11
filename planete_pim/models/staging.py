# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PlanetePimProductStaging(models.Model):
    _name = "planete.pim.product.staging"
    _description = "Planète PIM - Produit en Quarantaine"

    name = fields.Char(string="Nom")
    ean13 = fields.Char(string="EAN-13", index=True)
    default_code = fields.Char(string="Référence / SKU", index=True)
    
    # Champ pour expliquer pourquoi le produit est en quarantaine
    quarantine_reason = fields.Selection(
        selection=[
            ("no_ean", "Pas d'EAN valide"),
            ("duplicate_ean", "EAN en doublon (vidé)"),
            ("duplicate_ref", "Référence en doublon"),
            ("invalid_data", "Données invalides"),
            ("manual", "Ajouté manuellement"),
            ("other", "Autre"),
        ],
        string="Raison de quarantaine",
        index=True,
        help="Indique pourquoi ce produit est en quarantaine et n'a pas été importé directement",
    )
    quarantine_details = fields.Text(
        string="Détails quarantaine",
        help="Informations supplémentaires sur la raison de la mise en quarantaine",
    )
    original_ean = fields.Char(
        string="EAN original (avant nettoyage)",
        help="Si l'EAN a été vidé pour cause de doublon, cette colonne garde la valeur originale",
    )
    description = fields.Text(string="Description")
    brand_id = fields.Many2one("product.brand", string="Marque", ondelete="set null")
    categ_id = fields.Many2one("product.category", string="Catégorie", ondelete="set null")

    list_price = fields.Float(string="Prix de vente")
    standard_price = fields.Float(string="Coût")
    qty_available = fields.Float(string="Stock (fourni)")

    supplier_id = fields.Many2one("res.partner", string="Fournisseur", ondelete="set null")
    provider_id = fields.Many2one("ftp.provider", string="Provider (source)", ondelete="set null")

    file_name = fields.Char(string="Nom du fichier")
    row_number = fields.Integer(string="N° ligne")
    log_id = fields.Many2one("ftp.tariff.import.log", string="Journal d'import", ondelete="set null")

    state = fields.Selection(
        selection=[
            ("pending", "En attente"),
            ("validated", "Validé"),
            ("error", "Erreur"),
            ("applied", "Appliqué"),
        ],
        default="pending",
        index=True,
    )
    error_message = fields.Text(string="Erreurs de validation")
    data_json = fields.Json(string="Données source (JSON)")

    currency_id = fields.Many2one(
        "res.currency",
        string="Devise",
        default=lambda self: self.env.company.currency_id.id,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        default=lambda self: self.env.company.id,
    )
    product_tmpl_id = fields.Many2one("product.template", string="Produit créé", readonly=True, copy=False)
    staging_vendor_ids = fields.Many2many(
        "planete.pim.staging.vendor",
        string="Vendors (staging)",
        compute="_compute_staging_vendors",
        readonly=True,
    )

    def action_apply_to_products(self):
        """Apply validated staging rows to product.template:
        - Find or create product by barcode (EAN) or default_code
        - Update core fields (name, barcode, default_code, list/standard price, categ/brand)
        - Log price changes in planete.pim.price.history
        - Set import metadata on product
        - Mark staging row as applied or error with message
        """
        ProductTemplate = self.env["product.template"]
        PriceHistory = self.env["planete.pim.price.history"]
        for rec in self:
            try:
                if rec.state not in ("validated", "applied", "error"):
                    # allow applying even if pending, but primarily for validated
                    pass
                if not (rec.ean13 or rec.default_code):
                    raise ValueError(_("Aucun identifiant produit (EAN ou Référence)"))
                # Build search domain
                domain = []
                if rec.ean13 and rec.default_code:
                    domain = ["|", ("barcode", "=", rec.ean13), ("default_code", "=", rec.default_code)]
                elif rec.ean13:
                    domain = [("barcode", "=", rec.ean13)]
                else:
                    domain = [("default_code", "=", rec.default_code)]
                tmpl = ProductTemplate.search(domain, limit=1)
                old_price = tmpl.list_price if tmpl else 0.0
                vals = {
                    "name": rec.name or rec.ean13 or rec.default_code,
                    "barcode": rec.ean13 or False,
                    "default_code": rec.default_code or False,
                    "list_price": rec.list_price or 0.0,
                    "standard_price": rec.standard_price or 0.0,
                }
                if rec.categ_id:
                    vals["categ_id"] = rec.categ_id.id
                if rec.brand_id and "product_brand_id" in ProductTemplate._fields:
                    vals["product_brand_id"] = rec.brand_id.id
                # Enforce duplicate EAN policy (clear duplicates on other products if configured at provider level)
                try:
                    if rec.ean13 and rec.provider_id and getattr(rec.provider_id, "clear_duplicate_barcodes", False):
                        dups = ProductTemplate.search([("barcode", "=", rec.ean13)])
                        if tmpl:
                            dups = dups.filtered(lambda t: t.id != tmpl.id)
                        if dups:
                            dups.write({"barcode": False})
                except Exception:
                    # Do not block consolidation if clearance fails
                    pass
                if tmpl:
                    tmpl.write(vals)
                else:
                    tmpl = ProductTemplate.create(vals)
                # Price history logging
                if tmpl and float(old_price) != float(vals.get("list_price", 0.0)):
                    PriceHistory.create({
                        "product_tmpl_id": tmpl.id,
                        "product_id": getattr(tmpl, "product_variant_id", False) and tmpl.product_variant_id.id or False,
                        "ean13": rec.ean13,
                        "supplier_id": rec.supplier_id.id if rec.supplier_id else False,
                        "old_price": old_price,
                        "new_price": vals.get("list_price", 0.0),
                        "currency_id": rec.currency_id.id if rec.currency_id else self.env.company.currency_id.id,
                        "log_id": rec.log_id.id if rec.log_id else False,
                    })
                # Import metadata
                try:
                    ProductTemplate.set_import_metadata(
                        [tmpl.id],
                        log_id=rec.log_id.id if rec.log_id else None,
                        supplier_id=rec.supplier_id.id if rec.supplier_id else None,
                    )
                except Exception:
                    # do not block application if helper fails
                    pass
                rec.write({"state": "applied"})
            except Exception as e:
                rec.write({"state": "error", "error_message": str(e)})
        return True

    @api.depends("ean13")
    def _compute_staging_vendors(self):
        Vendor = self.env["planete.pim.staging.vendor"]
        for rec in self:
            if rec.ean13:
                rec.staging_vendor_ids = Vendor.search([("ean13", "=", rec.ean13)])
            else:
                rec.staging_vendor_ids = Vendor.browse()

    def action_delete_all_quarantine(self):
        """Supprime TOUS les produits en quarantaine par batch SQL direct.
        
        Contourne la limite Odoo de 20,000 enregistrements en utilisant
        des requêtes SQL DELETE par batch de 10,000.
        
        Utilisé pour nettoyer massivement la quarantaine (ex: 500k+ produits).
        """
        # Compter le total
        self.env.cr.execute("SELECT COUNT(*) FROM planete_pim_product_staging")
        total_count = self.env.cr.fetchone()[0]
        
        if total_count == 0:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Quarantaine vide"),
                    "message": _("Aucun produit en quarantaine à supprimer."),
                    "type": "info",
                    "sticky": False,
                }
            }
        
        # Demander confirmation
        return {
            "type": "ir.actions.act_window",
            "name": _("Confirmer la suppression massive"),
            "res_model": "planete.pim.staging.delete.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_total_count": total_count,
            }
        }

    def action_create_product(self):
        """Create or enrich a product from staging row.
        - Requires name and EAN-13
        - If product with same EAN exists: do not create; only add/update supplierinfo and overwrite stock
        - Else create template, then add supplierinfo and stock
        - Create brand/category/partner if missing (using data_json fallback)
        """
        self.ensure_one()
        rec = self

        def _ensure_ean13(value):
            if not value or len(value) != 13 or not value.isdigit():
                raise ValidationError(_("EAN-13 invalide: %s") % (value or ""))
            return value

        def _get_or_create_brand():
            if rec.brand_id:
                return rec.brand_id
            label = None
            try:
                label = rec.data_json.get("Marque") if rec.data_json else None
            except Exception:
                label = None
            if label:
                Brand = self.env["product.brand"]
                # Use alias-aware search method
                brand = Brand.find_by_name_or_alias(label, create_if_not_found=True)
                return brand
            return False

        def _get_or_create_categ():
            if rec.categ_id:
                return rec.categ_id
            label = None
            try:
                label = rec.data_json.get("Catégorie") if rec.data_json else None
            except Exception:
                label = None
            if label:
                Categ = self.env["product.category"]
                categ = Categ.search([("name", "=", label)], limit=1)
                if not categ:
                    categ = Categ.create({"name": label})
                return categ
            return False

        def _get_or_create_supplier():
            if rec.supplier_id:
                return rec.supplier_id
            # Accept multiple possible keys from JSON: "Fournisseur", "Vendor", "supplier", "vendor"
            label = None
            try:
                if rec.data_json:
                    for key in ("Fournisseur", "Vendor", "supplier", "vendor"):
                        if key in rec.data_json and rec.data_json.get(key):
                            label = rec.data_json.get(key)
                            if label:
                                break
            except Exception:
                label = None
            if label:
                Partner = self.env["res.partner"]
                # search loosely by name
                sup = Partner.search([("name", "ilike", label)], limit=1)
                if not sup:
                    vals = {"name": label, "supplier_rank": 1}
                    if rec.company_id:
                        vals["company_id"] = rec.company_id.id
                    # Ensure autopost_bills is set if the field exists (required NOT NULL)
                    if "autopost_bills" in Partner._fields:
                        vals["autopost_bills"] = False
                    sup = Partner.create(vals)
                return sup
            return False

        # Validations
        if not rec.name:
            raise UserError(_("Le champ Nom est obligatoire."))
        rec.ean13 = _ensure_ean13(rec.ean13)

        ProductTemplate = self.env["product.template"]
        SupplierInfo = self.env["product.supplierinfo"]

        # Determine brand/category/supplier, creating records if needed
        brand = _get_or_create_brand()
        categ = _get_or_create_categ()
        supplier = _get_or_create_supplier()

        # Search existing product by EAN (strict)
        tmpl = ProductTemplate.search([("barcode", "=", rec.ean13)], limit=1)
        created = False
        if tmpl:
            # Do not create new if EAN already exists: only enrich
            pass
        else:
            vals = {
                "name": rec.name,
                "barcode": rec.ean13,
                "default_code": rec.default_code or rec.ean13 or rec.name or False,
                "list_price": rec.list_price or 0.0,
                # set standard_price only at creation as requested
                "standard_price": rec.standard_price or 0.0,
                "sale_ok": False,
                "purchase_ok": True,
                # ⚠️ MULTI-SOCIÉTÉS: company_id=False pour partager entre IVS Pro et Planete Technologie
                "company_id": False,
            }
            if categ:
                vals["categ_id"] = categ.id
            if brand and "product_brand_id" in ProductTemplate._fields:
                vals["product_brand_id"] = brand.id
            tmpl = ProductTemplate.create(vals)
            created = True

        # Ensure product is storable (compat across Odoo versions)
        def _ensure_storable():
            try:
                if "detailed_type" in ProductTemplate._fields:
                    if getattr(tmpl, "detailed_type", None) != "product":
                        tmpl.sudo().write({"detailed_type": "product"})
                    # re-browse to ensure cache updated
                    tmpl_refreshed = ProductTemplate.browse(tmpl.id)
                    return getattr(tmpl_refreshed, "detailed_type", None) == "product"
                elif "type" in ProductTemplate._fields:
                    if getattr(tmpl, "type", None) != "product":
                        tmpl.sudo().write({"type": "product"})
                    # re-browse to ensure cache updated
                    tmpl_refreshed = ProductTemplate.browse(tmpl.id)
                    return getattr(tmpl_refreshed, "type", None) == "product"
            except Exception:
                return False
            return False

        storable_ok = _ensure_storable()

        # Ensure brand/default_code on template & variant to satisfy ivspro_profile constraint
        try:
            # set brand on template if field exists and not set
            if brand and "product_brand_id" in ProductTemplate._fields and not getattr(tmpl, "product_brand_id", False):
                tmpl.write({"product_brand_id": brand.id})
        except Exception:
            pass
        try:
            variant = tmpl.product_variant_id
            v_vals = {}
            # brand on variant if field exists and not set
            if brand and hasattr(variant, "_fields") and "product_brand_id" in variant._fields and not getattr(variant, "product_brand_id", False):
                v_vals["product_brand_id"] = brand.id
            # default_code on variant if field exists and not set (fallback to EAN or name)
            sku_fallback = rec.default_code or rec.ean13 or rec.name
            if hasattr(variant, "_fields") and "default_code" in variant._fields and not getattr(variant, "default_code", False) and sku_fallback:
                v_vals["default_code"] = sku_fallback
            if v_vals:
                variant.write(v_vals)
        except Exception:
            # do not block if variant enrichment fails
            pass

        # Re-enable sale_ok and ensure purchase_ok once brand/SKU set to satisfy ivspro_profile constraint
        try:
            to_write = {}
            if hasattr(tmpl, "_fields"):
                if "sale_ok" in tmpl._fields and not getattr(tmpl, "sale_ok", False):
                    to_write["sale_ok"] = True
                if "purchase_ok" in tmpl._fields and not getattr(tmpl, "purchase_ok", False):
                    to_write["purchase_ok"] = True
            if to_write:
                tmpl.write(to_write)
        except Exception:
            pass

        # Supplierinfo mapping on template
        if supplier:
            domain_si = [
                ("partner_id", "=", supplier.id),
                ("product_tmpl_id", "=", tmpl.id),
            ]
            si = SupplierInfo.search(domain_si, limit=1)
            si_vals = {
                "partner_id": supplier.id,
                "product_tmpl_id": tmpl.id,
                "min_qty": rec.qty_available or 0.0,
                "price": rec.standard_price or 0.0,
            }
            if rec.currency_id:
                si_vals["currency_id"] = rec.currency_id.id
            if si:
                si.write({
                    "min_qty": si_vals["min_qty"],
                    "price": si_vals["price"],
                    "currency_id": si_vals.get("currency_id", si.currency_id.id),
                })
            else:
                SupplierInfo.create(si_vals)

        # Metadata
        try:
            ProductTemplate.set_import_metadata(
                [tmpl.id],
                log_id=rec.log_id.id if rec.log_id else None,
                supplier_id=supplier.id if supplier else None,
            )
        except Exception:
            pass

        # Overwrite stock to qty_available in main warehouse
        if rec.qty_available is not None:
            Warehouse = self.env["stock.warehouse"]
            wh = Warehouse.search([("company_id", "=", rec.company_id.id)], limit=1) if rec.company_id else Warehouse.search([], limit=1)
            if not wh:
                raise UserError(_("Aucun entrepôt trouvé pour ajuster le stock."))
            location = wh.lot_stock_id
            product = tmpl.product_variant_id

            # Ensure product is storable before adjusting stock
            if not storable_ok:
                # retry once in case fields were recomputed
                storable_ok = _ensure_storable()
            # Odoo 17+/18: adjust via stock.quant inventory fields (stock.inventory removed)
            if storable_ok:
                StockQuant = self.env["stock.quant"].with_context(inventory_mode=True)
                domain = [
                    ("product_id", "=", product.id),
                    ("location_id", "=", location.id),
                    ("lot_id", "=", False),
                    ("owner_id", "=", False),
                    ("package_id", "=", False),
                ]
                quant = StockQuant.search(domain, limit=1)
                if not quant:
                    # create an empty quant in inventory mode
                    quant = StockQuant.create({
                        "product_id": product.id,
                        "location_id": location.id,
                    })
                # set target quantity using available field name
                qty_vals = {}
                if "inventory_quantity_set" in StockQuant._fields:
                    qty_vals["inventory_quantity_set"] = rec.qty_available
                elif "inventory_quantity" in StockQuant._fields:
                    qty_vals["inventory_quantity"] = rec.qty_available
                if qty_vals:
                    quant.write(qty_vals)
                # apply inventory
                try:
                    quant.action_apply_inventory()
                except Exception:
                    # if method name differs or not available, ignore (qty will be handled by Odoo stock layer)
                    pass
            else:
                # skip adjustment for consumables/services without failing
                pass

        # Link staging to product and mark applied
        rec.write({
            "product_tmpl_id": tmpl.id,
            "state": "applied",
            "error_message": False,
        })

        # Feedback: open the product
        action = self.env.ref("product.product_template_action_all").read()[0]
        action["res_id"] = tmpl.id
        action["views"] = [(self.env.ref("product.product_template_form_view").id, "form")]
        return action


class PlanetePimImportHistory(models.Model):
    _name = "planete.pim.import.history"
    _description = "Planète PIM - Historique d'import"
    _order = "create_date desc, id desc"

    name = fields.Char(
        required=True,
        default=lambda self: _("Import %s") % fields.Datetime.now(),
    )
    log_id = fields.Many2one(
        "ftp.tariff.import.log",
        string="Journal d'import",
        ondelete="set null",
    )
    provider_id = fields.Many2one(
        "ftp.provider",
        string="Provider",
        ondelete="set null",
    )
    file_name = fields.Char(string="Nom du fichier")
    total_lines = fields.Integer(string="Lignes (total)")
    success_count = fields.Integer(string="OK (validations)")
    error_count = fields.Integer(string="Erreurs")
    created_count = fields.Integer(string="Créés (staging)")
    updated_count = fields.Integer(string="Mis à jour")
    # Colonnes détaillées pour traçabilité complète
    skipped_existing_count = fields.Integer(
        string="Ignorés (existants)",
        help="Nombre de lignes ignorées car le produit existait déjà en base (EAN ou référence)",
    )
    skipped_not_found_count = fields.Integer(
        string="Ignorés (non trouvés)",
        help="Nombre de lignes ignorées car le produit n'a pas été trouvé (DELTA uniquement)",
    )
    quarantined_count = fields.Integer(
        string="Quarantaine",
        help="Nombre de produits envoyés en quarantaine (sans EAN, doublons, etc.)",
    )
    message = fields.Text(string="Message")
    # Nouvelles marques créées pendant l'import (pour notification)
    new_brands_created = fields.Text(
        string="Nouvelles marques créées",
        help="Liste des marques qui n'existaient pas et ont été créées pendant cet import. "
             "À vérifier pour ajouter les alias si nécessaire.",
    )
    new_brands_count = fields.Integer(
        string="Nb marques créées",
        help="Nombre de nouvelles marques créées pendant l'import",
    )
    # =========================================================================
    # NOUVEAU: Compteurs détaillés et lignes d'erreur
    # =========================================================================
    deduped_count = fields.Integer(
        string="Lignes dédupliquées",
        default=0,
        help="Nombre de lignes identiques supprimées avant traitement (doublons dans le fichier)",
    )
    error_line_ids = fields.One2many(
        'planete.pim.import.error.line',
        'history_id',
        string="Détails des anomalies",
        help="Détails de chaque ligne qui n'a pas été importée normalement",
    )
    error_line_count = fields.Integer(
        string="Nb lignes anomalie",
        compute='_compute_error_line_count',
        store=True,
        help="Nombre total de lignes avec anomalies (ignorées, quarantaine, dédupliquées, etc.)",
    )
    
    @api.depends('error_line_ids')
    def _compute_error_line_count(self):
        for rec in self:
            rec.error_line_count = len(rec.error_line_ids)
    
    def action_export_errors_csv(self):
        """Exporte les détails des erreurs au format CSV."""
        self.ensure_one()
        import csv
        import base64
        from io import StringIO
        
        if not self.error_line_ids:
            raise UserError(_("Aucune anomalie à exporter pour cet import."))
        
        output = StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_MINIMAL)
        
        # En-têtes
        writer.writerow([
            'N° Ligne',
            'EAN',
            'Référence',
            'Nom produit',
            'Type anomalie',
            'Détails',
            'Action',
        ])
        
        # Données
        for line in self.error_line_ids:
            writer.writerow([
                line.row_number or '',
                line.ean or '',
                line.reference or '',
                line.product_name or '',
                dict(line._fields['error_type'].selection).get(line.error_type, ''),
                line.error_details or '',
                dict(line._fields['action_taken'].selection).get(line.action_taken, ''),
            ])
        
        # Créer l'attachment
        csv_data = output.getvalue().encode('utf-8')
        attachment = self.env['ir.attachment'].create({
            'name': f'erreurs_import_{self.id}_{fields.Date.today()}.csv',
            'datas': base64.b64encode(csv_data),
            'mimetype': 'text/csv',
            'res_model': self._name,
            'res_id': self.id,
        })
        
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }


class PlanetePimPriceHistory(models.Model):
    _name = "planete.pim.price.history"
    _description = "Planète PIM - Historique des prix"
    _order = "create_date desc, id desc"

    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Produit",
        ondelete="set null",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Variante",
        ondelete="set null",
    )
    ean13 = fields.Char(string="EAN-13", index=True)
    supplier_id = fields.Many2one(
        "res.partner",
        string="Fournisseur",
        ondelete="set null",
    )
    old_price = fields.Float(string="Ancien prix")
    new_price = fields.Float(string="Nouveau prix")
    currency_id = fields.Many2one(
        "res.currency",
        string="Devise",
        default=lambda self: self.env.company.currency_id.id,
    )
    changed_at = fields.Datetime(
        string="Date de changement",
        default=lambda self: fields.Datetime.now(),
    )
    log_id = fields.Many2one(
        "ftp.tariff.import.log",
        string="Journal d'import",
        ondelete="set null",
    )
