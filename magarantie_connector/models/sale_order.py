# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    magarantie_sale_ids = fields.One2many(
        'magarantie.sale',
        'sale_order_id',
        string='MaGarantie Warranty Sales',
    )
    magarantie_sale_count = fields.Integer(
        string='Warranty Sales',
        compute='_compute_magarantie_sale_count',
    )
    has_magarantie_eligible = fields.Boolean(
        compute='_compute_has_magarantie_eligible',
    )

    @api.depends('magarantie_sale_ids')
    def _compute_magarantie_sale_count(self):
        for rec in self:
            rec.magarantie_sale_count = len(rec.magarantie_sale_ids)

    @api.depends('order_line.product_template_id.magarantie_eligible')
    def _compute_has_magarantie_eligible(self):
        for rec in self:
            rec.has_magarantie_eligible = any(
                line.product_template_id.magarantie_eligible
                for line in rec.order_line
                if line.product_template_id
            )

    def action_view_magarantie_sales(self):
        """View MaGarantie warranty sales linked to this order."""
        self.ensure_one()
        return {
            'name': _('Warranty Sales'),
            'type': 'ir.actions.act_window',
            'res_model': 'magarantie.sale',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id},
        }

    def action_open_magarantie_wizard(self):
        """Open the warranty selection wizard."""
        self.ensure_one()
        return {
            'name': _('Propose Warranty Extensions'),
            'type': 'ir.actions.act_window',
            'res_model': 'magarantie.warranty.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_sale_order_id': self.id,
            },
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    magarantie_sale_id = fields.Many2one(
        'magarantie.sale',
        string='Warranty Sale',
    )
    is_magarantie_warranty = fields.Boolean(
        string='Is Warranty Line',
        default=False,
    )

    def _prepare_invoice_line(self, **optional_values):
        """Propagate MaGarantie fields to invoice line."""
        vals = super()._prepare_invoice_line(**optional_values)
        if self.is_magarantie_warranty:
            vals['is_magarantie_warranty'] = True
        if self.magarantie_sale_id:
            vals['magarantie_sale_id'] = self.magarantie_sale_id.id
        return vals
