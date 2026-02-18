# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MaGarantieWarranty(models.Model):
    _name = 'magarantie.warranty'
    _description = 'MaGarantie Warranty Offer'
    _order = 'category_id, prix'

    idgarantie = fields.Char(
        string='Warranty ID (API)',
        required=True,
        index=True,
    )
    libelle = fields.Char(string='Label')
    category_id = fields.Many2one(
        'magarantie.category',
        string='Category',
        ondelete='cascade',
        index=True,
    )
    rubrique = fields.Char(
        related='category_id.rubrique',
        store=True,
        string='Rubrique Code',
    )
    prix = fields.Float(string='Price (EUR)', digits=(12, 2))
    min_tranche = fields.Float(
        string='Min Product Price',
        digits=(12, 2),
        help="Minimum product price for this warranty to apply",
    )
    max_tranche = fields.Float(
        string='Max Product Price',
        digits=(12, 2),
        help="Maximum product price for this warranty to apply",
    )
    programme = fields.Char(string='Programme')
    type_garantie = fields.Char(string='Warranty Type')
    duree = fields.Char(string='Duration')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('idgarantie_unique', 'unique(idgarantie)', 'Warranty ID must be unique!'),
    ]

    @api.depends('libelle', 'prix', 'category_id.name')
    def _compute_display_name(self):
        for rec in self:
            cat_name = rec.category_id.name or ''
            label = rec.libelle or rec.idgarantie or ''
            if cat_name:
                rec.display_name = "%s (%s) - %.2f EUR" % (label, cat_name, rec.prix)
            else:
                rec.display_name = "%s - %.2f EUR" % (label, rec.prix)

    @api.model
    def action_sync_from_api(self):
        """Sync all warranties from the MaGarantie API."""
        api_client = self.env['res.config.settings']._get_magarantie_api()
        try:
            warranties = api_client.get_all_garanties()
            if not isinstance(warranties, list):
                warranties = [warranties] if warranties else []

            synced = 0
            for gar in warranties:
                idgarantie = str(gar.get('idgarantie', ''))
                if not idgarantie:
                    continue

                rubrique_code = gar.get('rubrique', '')
                category = False
                if rubrique_code:
                    category = self.env['magarantie.category'].search(
                        [('rubrique', '=', rubrique_code)], limit=1
                    )

                vals = {
                    'idgarantie': idgarantie,
                    'libelle': gar.get('libelle', '') or gar.get('titre', '') or '',
                    'category_id': category.id if category else False,
                    'prix': float(gar.get('prix', 0) or 0),
                    'min_tranche': float(gar.get('min_tranche', 0) or 0),
                    'max_tranche': float(gar.get('max_tranche', 0) or 0),
                    'programme': gar.get('programme', '') or '',
                    'type_garantie': gar.get('type_garantie', '') or gar.get('type', '') or '',
                    'duree': gar.get('duree', '') or '',
                }

                existing = self.search([('idgarantie', '=', idgarantie)], limit=1)
                if existing:
                    existing.write(vals)
                else:
                    self.create(vals)
                synced += 1

            _logger.info("MaGarantie: Synced %d warranties", synced)
            return synced
        except UserError:
            raise
        except Exception as e:
            _logger.error("MaGarantie warranty sync failed: %s", str(e))
            raise UserError(_("Warranty sync failed: %s") % str(e))

    def action_refresh_details(self):
        """Refresh warranty details from the API."""
        self.ensure_one()
        api_client = self.env['res.config.settings']._get_magarantie_api()
        details = api_client.get_infos_garantie(self.idgarantie)
        if details and isinstance(details, dict):
            vals = {}
            if details.get('titre'):
                vals['libelle'] = details['titre']
            if details.get('prix'):
                vals['prix'] = float(details['prix'])
            if details.get('programme'):
                vals['programme'] = details['programme']
            if details.get('type_garantie'):
                vals['type_garantie'] = details['type_garantie']
            if details.get('min_tranche'):
                vals['min_tranche'] = float(details['min_tranche'])
            if details.get('max_tranche'):
                vals['max_tranche'] = float(details['max_tranche'])
            if vals:
                self.write(vals)
