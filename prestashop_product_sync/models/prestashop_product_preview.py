import json
import logging

import odoo
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopProductPreview(models.Model):
    _name = 'prestashop.product.preview'
    _description = 'PrestaShop Product Preview'
    _order = 'state_sequence, prestashop_id asc'
    _rec_name = 'name'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True,
        ondelete='cascade', index=True,
    )
    prestashop_id = fields.Char('PS ID', required=True, index=True)
    name = fields.Char('Product Name')
    reference = fields.Char('Reference / SKU')
    price = fields.Float('Price (HT)')
    ean13 = fields.Char('EAN13')
    active_in_ps = fields.Boolean('Active in PS', default=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('importing', 'Importing...'),
        ('imported', 'Imported'),
        ('updated', 'Updated'),
        ('error', 'Error'),
        ('skipped', 'Skipped'),
    ], default='pending', string='Status', index=True)
    state_sequence = fields.Integer(
        compute='_compute_state_sequence', store=True,
        help="Used for ordering: importing first, then pending, then errors, then done.",
    )
    imported_product_id = fields.Many2one(
        'product.template', 'Odoo Product', readonly=True,
    )
    error_message = fields.Text('Error Details')
    import_date = fields.Datetime('Import Date', readonly=True)
    raw_data = fields.Text('Raw API Data', readonly=True)

    _sql_constraints = [
        ('unique_ps_product_instance',
         'unique(instance_id, prestashop_id)',
         'This product is already in the preview for this instance.'),
    ]

    @api.depends('state')
    def _compute_state_sequence(self):
        order = {
            'importing': 0,
            'pending': 1,
            'error': 2,
            'imported': 3,
            'updated': 4,
            'skipped': 5,
        }
        for rec in self:
            rec.state_sequence = order.get(rec.state, 9)

    def action_debug_fetch(self):
        """Fetch full API response and store as raw_data for debugging."""
        self.ensure_one()
        instance = self.instance_id
        try:
            data = instance._api_get_long(
                'products', resource_id=str(self.prestashop_id),
                params={'display': 'full'},
                timeout=120,
            )
            raw = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            self.write({'raw_data': raw})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Raw Data Fetched'),
                    'message': _('Check the "Raw Data" tab on this preview record.'),
                    'type': 'info',
                    'sticky': False,
                },
            }
        except Exception as exc:
            self.write({
                'raw_data': 'ERROR: %s' % exc,
                'error_message': str(exc),
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Fetch Failed'),
                    'message': str(exc),
                    'type': 'danger',
                    'sticky': True,
                },
            }

    def action_import_single(self):
        """Import this single product from PrestaShop."""
        self.ensure_one()
        instance = self.instance_id
        try:
            self.write({'state': 'importing', 'error_message': False})
            self.env.cr.commit()

            existing = self.env['product.template'].search([
                ('prestashop_id', '=', self.prestashop_id),
                ('prestashop_instance_id', '=', instance.id),
            ], limit=1)

            ps_product = instance._fetch_single_product_full(self.prestashop_id)

            # Store raw data for debugging
            try:
                self.raw_data = json.dumps(
                    ps_product, indent=2, ensure_ascii=False, default=str,
                )
            except Exception:
                pass

            if not ps_product or not ps_product.get('id'):
                self.write({
                    'state': 'error',
                    'error_message': (
                        'Empty API response — product may have been deleted '
                        'or deactivated in PrestaShop (PS-%s). '
                        'Use "Debug Fetch" to see raw API response.'
                    ) % self.prestashop_id,
                })
                self.env.cr.commit()
                return

            product_tmpl = instance._sync_single_product(ps_product)
            self.write({
                'state': 'updated' if existing else 'imported',
                'imported_product_id': product_tmpl.id if product_tmpl else False,
                'import_date': fields.Datetime.now(),
                'name': product_tmpl.name if product_tmpl else self.name,
                'error_message': False,
            })
            self.env.cr.commit()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Product Imported'),
                    'message': self.name,
                    'type': 'success',
                    'sticky': False,
                },
            }

        except Exception as exc:
            self.write({'state': 'error', 'error_message': str(exc)})
            self.env.cr.commit()
            _logger.error("Import failed for PS-%s: %s", self.prestashop_id, exc)

    def action_import_selected(self):
        """Import selected preview records in background."""
        pending = self.filtered(lambda r: r.state in ('pending', 'error'))
        if not pending:
            raise UserError(_("No pending products to import in selection."))

        for instance in pending.mapped('instance_id'):
            previews = pending.filtered(lambda r: r.instance_id == instance)
            instance._import_previews_background(previews.ids)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Started'),
                'message': _(
                    '%d products are being imported in background. '
                    'The list refreshes automatically.'
                ) % len(pending),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_skip(self):
        self.write({'state': 'skipped'})

    def action_reset(self):
        self.write({'state': 'pending', 'error_message': False})

    def action_view_product(self):
        """Open the imported Odoo product."""
        self.ensure_one()
        if not self.imported_product_id:
            raise UserError(_("Product not yet imported."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': self.imported_product_id.id,
            'view_mode': 'form',
        }
