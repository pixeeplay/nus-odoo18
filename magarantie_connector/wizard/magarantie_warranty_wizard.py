# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MaGarantieWarrantyWizard(models.TransientModel):
    _name = 'magarantie.warranty.wizard'
    _description = 'MaGarantie Warranty Selection Wizard'

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
    )
    line_ids = fields.One2many(
        'magarantie.warranty.wizard.line',
        'wizard_id',
        string='Eligible Products',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if res.get('sale_order_id'):
            order = self.env['sale.order'].browse(res['sale_order_id'])
            lines = []
            for sol in order.order_line:
                tmpl = sol.product_template_id
                if not tmpl or not tmpl.magarantie_eligible or sol.is_magarantie_warranty:
                    continue

                # Find matching warranties by category and price tranche
                warranties = self.env['magarantie.warranty'].search([
                    ('category_id', '=', tmpl.magarantie_category_id.id),
                    ('min_tranche', '<=', sol.price_unit),
                    ('max_tranche', '>=', sol.price_unit),
                    ('active', '=', True),
                ])
                if not warranties:
                    continue

                lines.append((0, 0, {
                    'sale_order_line_id': sol.id,
                    'product_name': sol.product_id.display_name,
                    'product_price': sol.price_unit,
                    'category_name': tmpl.magarantie_category_id.name,
                    'selected': False,
                    'warranty_id': warranties[0].id,
                    'available_warranty_ids': [(6, 0, warranties.ids)],
                }))
            res['line_ids'] = lines
        return res

    def action_add_warranties(self):
        """Add selected warranty lines to the sale order."""
        self.ensure_one()
        order = self.sale_order_id

        if order.state not in ('draft', 'sent'):
            raise UserError(
                _("Warranties can only be added to draft or sent orders.")
            )

        selected_lines = self.line_ids.filtered('selected')
        if not selected_lines:
            raise UserError(_("Please select at least one warranty to add."))

        warranty_product = self._get_or_create_warranty_product()
        added = 0

        for wiz_line in selected_lines:
            warranty = wiz_line.warranty_id
            sol = wiz_line.sale_order_line_id
            if not warranty:
                continue

            partner = order.partner_id

            # Split partner name into first/last name
            name_parts = (partner.name or '').split()
            prenom = name_parts[0] if name_parts else ''
            nom = ' '.join(name_parts[1:]) if len(name_parts) > 1 else prenom

            # Get brand if product_brand module is installed
            marque = ''
            if hasattr(sol.product_id, 'product_brand_id') and sol.product_id.product_brand_id:
                marque = sol.product_id.product_brand_id.name

            # Create magarantie.sale record
            mag_sale = self.env['magarantie.sale'].create({
                'sale_order_id': order.id,
                'sale_order_line_id': sol.id,
                'partner_id': partner.id,
                'product_template_id': sol.product_template_id.id,
                'warranty_id': warranty.id,
                'nom': nom,
                'prenom': prenom,
                'email': partner.email or '',
                'telephone': partner.phone or partner.mobile or '',
                'adresse': partner.street or '',
                'adresse2': partner.street2 or '',
                'code_postal': partner.zip or '',
                'ville': partner.city or '',
                'rubrique': warranty.rubrique or '',
                'idgarantie': warranty.idgarantie,
                'garantie_prix': warranty.prix,
                'produit_prix': sol.price_unit,
                'date_achat': order.date_order.date() if order.date_order else fields.Date.today(),
                'produit_marque': marque,
                'produit_modele': sol.product_id.default_code or '',
                'state': 'draft',
            })

            # Add warranty as a sale order line
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': warranty_product.id,
                'name': "Extension de garantie: %s - %s" % (
                    warranty.libelle or warranty.idgarantie,
                    sol.product_id.display_name,
                ),
                'product_uom_qty': 1,
                'price_unit': warranty.prix,
                'is_magarantie_warranty': True,
                'magarantie_sale_id': mag_sale.id,
            })
            added += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Warranties Added'),
                'message': _('%d warranty extension(s) added to the order.') % added,
                'type': 'success',
                'sticky': False,
            },
        }

    def _get_or_create_warranty_product(self):
        """Get or create the service product used for warranty order lines."""
        product = self.env['product.product'].search([
            ('default_code', '=', 'MAGARANTIE-WARRANTY'),
        ], limit=1)
        if not product:
            product = self.env['product.product'].create({
                'name': 'Extension de Garantie (MaGarantie)',
                'default_code': 'MAGARANTIE-WARRANTY',
                'type': 'service',
                'list_price': 0.0,
                'taxes_id': [(5, 0, 0)],
                'purchase_ok': False,
                'sale_ok': True,
            })
        return product


class MaGarantieWarrantyWizardLine(models.TransientModel):
    _name = 'magarantie.warranty.wizard.line'
    _description = 'MaGarantie Warranty Wizard Line'

    wizard_id = fields.Many2one(
        'magarantie.warranty.wizard',
        ondelete='cascade',
    )
    sale_order_line_id = fields.Many2one(
        'sale.order.line',
        string='Order Line',
    )
    product_name = fields.Char(string='Product', readonly=True)
    product_price = fields.Float(string='Product Price', readonly=True)
    category_name = fields.Char(string='Category', readonly=True)
    selected = fields.Boolean(string='Add Warranty', default=False)
    warranty_id = fields.Many2one(
        'magarantie.warranty',
        string='Warranty Offer',
    )
    available_warranty_ids = fields.Many2many(
        'magarantie.warranty',
        string='Available Warranties',
    )
    warranty_price = fields.Float(
        related='warranty_id.prix',
        string='Warranty Price',
        readonly=True,
    )
