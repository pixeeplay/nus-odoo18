# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PrestaShopProductDashboard(models.TransientModel):
    _name = 'prestashop.product.dashboard'
    _description = 'PrestaShop Product Sync Dashboard'

    # Instance
    instance_id = fields.Many2one('prestashop.instance', string='Instance')
    instance_url = fields.Char('Store URL', readonly=True)
    instance_connected = fields.Boolean('Connected', readonly=True)
    last_product_sync = fields.Datetime('Last Product Sync', readonly=True)
    last_order_sync = fields.Datetime('Last Order Sync', readonly=True)
    import_running = fields.Boolean('Import Running', readonly=True)

    # Product stats
    total_ps_products = fields.Integer('Total in Preview', readonly=True)
    total_imported = fields.Integer('Imported', readonly=True)
    total_pending = fields.Integer('Pending', readonly=True)
    total_errors = fields.Integer('Errors', readonly=True)
    total_skipped = fields.Integer('Skipped', readonly=True)
    total_odoo_products = fields.Integer('In Odoo', readonly=True)
    total_price_mismatch = fields.Integer('Price Mismatch', readonly=True)

    # Order stats
    total_orders = fields.Integer('Total Orders', readonly=True)
    recent_orders_7d = fields.Integer('Recent (7d)', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        # Get first active instance
        instance = self.env['prestashop.instance'].search([
            ('active', '=', True),
        ], limit=1, order='id asc')

        if instance:
            res['instance_id'] = instance.id
            res['instance_url'] = (instance.url or '').rstrip('/').replace('/api', '')
            res['instance_connected'] = bool(instance.url and instance.api_key)
            res['last_product_sync'] = instance.last_product_sync_date
            res['last_order_sync'] = instance.last_sync_date
            res['import_running'] = instance.import_running

            # Preview stats
            Preview = self.env['prestashop.product.preview']
            domain_base = [('instance_id', '=', instance.id)]
            res['total_ps_products'] = Preview.search_count(domain_base)
            res['total_imported'] = Preview.search_count(
                domain_base + [('state', 'in', ('imported', 'updated'))])
            res['total_pending'] = Preview.search_count(
                domain_base + [('state', '=', 'pending')])
            res['total_errors'] = Preview.search_count(
                domain_base + [('state', '=', 'error')])
            res['total_skipped'] = Preview.search_count(
                domain_base + [('state', '=', 'skipped')])

            # Odoo products linked to this instance
            res['total_odoo_products'] = self.env['product.template'].search_count([
                ('prestashop_instance_id', '=', instance.id),
            ])

            # Price mismatch
            res['total_price_mismatch'] = Preview.search_count(
                domain_base + [
                    ('price_match', '=', False),
                    ('imported_product_id', '!=', False),
                ]
            )

            # Order stats
            res['total_orders'] = self.env['sale.order'].search_count([
                ('prestashop_instance_id', '=', instance.id),
            ])
            seven_days_ago = fields.Datetime.now() - timedelta(days=7)
            res['recent_orders_7d'] = self.env['sale.order'].search_count([
                ('prestashop_instance_id', '=', instance.id),
                ('date_order', '>=', seven_days_ago),
            ])

        return res

    def _get_instance(self):
        if self.instance_id:
            return self.instance_id
        instance = self.env['prestashop.instance'].search([
            ('active', '=', True),
        ], limit=1)
        if not instance:
            raise UserError(_("No active PrestaShop instance found."))
        return instance

    def action_fetch_products(self):
        return self._get_instance().action_fetch_product_previews()

    def action_import_all(self):
        return self._get_instance().action_import_all_previews()

    def action_test_connection(self):
        return self._get_instance().action_test_connection()

    def action_open_previews(self):
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Product Preview'),
            'res_model': 'prestashop.product.preview',
            'view_mode': 'list,form',
            'domain': [('instance_id', '=', instance.id)],
            'context': {'default_instance_id': instance.id},
            'target': 'current',
        }

    def action_open_products(self):
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('PrestaShop Products'),
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [('prestashop_instance_id', '=', instance.id)],
            'target': 'current',
        }

    def action_open_orders(self):
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('PrestaShop Orders'),
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('prestashop_instance_id', '=', instance.id)],
            'target': 'current',
        }

    def action_open_errors(self):
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Import Errors'),
            'res_model': 'prestashop.product.preview',
            'view_mode': 'list,form',
            'domain': [
                ('instance_id', '=', instance.id),
                ('state', '=', 'error'),
            ],
            'target': 'current',
        }

    def action_open_config(self):
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Instance Configuration'),
            'res_model': 'prestashop.instance',
            'res_id': instance.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_mappings(self):
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Field Mappings'),
            'res_model': 'prestashop.field.mapping',
            'view_mode': 'list,form',
            'domain': [('instance_id', '=', instance.id)],
            'target': 'current',
        }

    def action_open_price_mismatches(self):
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Price Mismatches'),
            'res_model': 'prestashop.product.preview',
            'view_mode': 'list,form',
            'domain': [
                ('instance_id', '=', instance.id),
                ('price_match', '=', False),
                ('imported_product_id', '!=', False),
            ],
            'target': 'current',
        }

    def action_retry_errors(self):
        instance = self._get_instance()
        errors = self.env['prestashop.product.preview'].search([
            ('instance_id', '=', instance.id),
            ('state', '=', 'error'),
        ])
        if errors:
            errors.write({'state': 'pending', 'error_message': False})
        return self._refresh()

    def _refresh(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('PrestaShop Dashboard'),
            'res_model': 'prestashop.product.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
