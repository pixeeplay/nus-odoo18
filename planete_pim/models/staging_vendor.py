# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class PlanetePimStagingVendor(models.Model):
    _name = "planete.pim.staging.vendor"
    _description = "Planète PIM - Staging Vendors per EAN/Provider"
    _order = "ean13, provider_id"

    name = fields.Char(string="Name", compute="_compute_name", store=True)
    ean13 = fields.Char(string="EAN-13", index=True, required=True)
    provider_id = fields.Many2one("ftp.provider", string="Provider", required=True, ondelete="cascade", index=True)
    supplier_id = fields.Many2one("res.partner", string="Vendor", ondelete="set null", index=True)
    quantity = fields.Float(string="Quantity")
    price = fields.Float(string="Price")
    currency_id = fields.Many2one("res.currency", string="Currency", default=lambda self: self.env.company.currency_id.id)
    last_log_id = fields.Many2one("ftp.tariff.import.log", string="Last Import Log", ondelete="set null")
    company_id = fields.Many2one("res.company", string="Company", default=lambda self: self.env.company.id)

    _sql_constraints = [
        ("uniq_vendor_by_ean_provider", "unique(ean13, provider_id)", "The Vendor line must be unique per EAN and Provider."),
    ]

    @api.depends("ean13", "provider_id", "supplier_id")
    def _compute_name(self):
        for rec in self:
            vendor = rec.supplier_id.name or (rec.provider_id and rec.provider_id.name) or ""
            rec.name = f"{vendor} / {rec.ean13 or ''}".strip()

    @api.model
    def upsert_from_import(self, *, ean13, provider_id, supplier_id=None, quantity=None, price=None, currency_id=None, log_id=None):
        """Create/update the vendor entry for an EAN+provider during import."""
        if not ean13 or not provider_id:
            return False
        vals = {
            "supplier_id": supplier_id or False,
            "quantity": quantity if quantity is not None else 0.0,
            "price": price if price is not None else 0.0,
            "currency_id": currency_id or self.env.company.currency_id.id,
            "last_log_id": log_id or False,
            "company_id": self.env.company.id,
        }
        existing = self.search([("ean13", "=", ean13), ("provider_id", "=", provider_id)], limit=1)
        if existing:
            existing.write(vals)
            return existing
        vals.update({"ean13": ean13, "provider_id": provider_id})
        return self.create(vals)
