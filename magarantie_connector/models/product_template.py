# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    magarantie_category_id = fields.Many2one(
        'magarantie.category',
        string='MaGarantie Category',
        help="MaGarantie warranty category (rubrique) for this product",
    )
    magarantie_rubrique = fields.Char(
        related='magarantie_category_id.rubrique',
        string='Rubrique Code',
        store=True,
    )
    magarantie_warranty_ids = fields.Many2many(
        'magarantie.warranty',
        string='Matching Warranties',
        compute='_compute_magarantie_warranty_ids',
        help="Warranties matching this product's category and price range",
    )
    magarantie_warranty_count = fields.Integer(
        string='Available Warranties',
        compute='_compute_magarantie_warranty_ids',
    )
    magarantie_eligible = fields.Boolean(
        string='Warranty Eligible',
        compute='_compute_magarantie_warranty_ids',
    )
    magarantie_best_warranty_id = fields.Many2one(
        'magarantie.warranty',
        string='Recommended Warranty',
        compute='_compute_magarantie_warranty_ids',
        help="Cheapest matching warranty for this product",
    )
    magarantie_best_price = fields.Float(
        string='Warranty Price',
        compute='_compute_magarantie_warranty_ids',
        digits=(12, 2),
        help="Price of the cheapest matching warranty",
    )

    @api.depends('magarantie_category_id', 'list_price')
    def _compute_magarantie_warranty_ids(self):
        Warranty = self.env['magarantie.warranty']
        for rec in self:
            if rec.magarantie_category_id and rec.list_price > 0:
                warranties = Warranty.search([
                    ('category_id', '=', rec.magarantie_category_id.id),
                    ('min_tranche', '<=', rec.list_price),
                    ('max_tranche', '>=', rec.list_price),
                    ('active', '=', True),
                ], order='prix asc')
                rec.magarantie_warranty_ids = warranties
                rec.magarantie_warranty_count = len(warranties)
                rec.magarantie_eligible = bool(warranties)
                rec.magarantie_best_warranty_id = warranties[0] if warranties else False
                rec.magarantie_best_price = warranties[0].prix if warranties else 0.0
            else:
                rec.magarantie_warranty_ids = Warranty
                rec.magarantie_warranty_count = 0
                rec.magarantie_eligible = False
                rec.magarantie_best_warranty_id = False
                rec.magarantie_best_price = 0.0

    def action_view_magarantie_warranties(self):
        """Open matching warranties for this product."""
        self.ensure_one()
        domain = [('category_id', '=', self.magarantie_category_id.id)]
        if self.list_price > 0:
            domain += [
                ('min_tranche', '<=', self.list_price),
                ('max_tranche', '>=', self.list_price),
            ]
        return {
            'name': _('Warranties - %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'magarantie.warranty',
            'view_mode': 'list,form',
            'domain': domain,
            'context': {'default_category_id': self.magarantie_category_id.id},
        }

    def action_sync_magarantie_warranties(self):
        """Sync warranties from the API."""
        self.ensure_one()
        if not self.magarantie_category_id:
            raise UserError(_("Please select a MaGarantie category first."))
        self.env['magarantie.warranty'].action_sync_from_api()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sync Complete'),
                'message': _('Warranties refreshed from MaGarantie.'),
                'type': 'success',
                'sticky': False,
            },
        }
