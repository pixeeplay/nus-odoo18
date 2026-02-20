# -*- coding: utf-8 -*-
import logging
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


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

    # Export stats
    total_export_enabled = fields.Integer('Export Enabled', readonly=True)
    total_exported = fields.Integer('Exported to PS', readonly=True)
    total_export_modified = fields.Integer('Modified (needs re-export)', readonly=True)
    total_export_errors = fields.Integer('Export Errors', readonly=True)
    total_export_queued = fields.Integer('Queued for Export', readonly=True)
    export_running = fields.Boolean('Export Running', readonly=True)
    last_product_export = fields.Datetime('Last Product Export', readonly=True)
    last_stock_export = fields.Datetime('Last Stock Export', readonly=True)
    last_price_export = fields.Datetime('Last Price Export', readonly=True)

    # Bidirectional sync summary
    total_imported_from_ps = fields.Integer('Importés depuis PS', readonly=True)
    total_exported_to_ps = fields.Integer('Exportés vers PS', readonly=True)

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

            # Export stats
            ProductTmpl = self.env['product.template']
            res['total_export_enabled'] = ProductTmpl.search_count([
                ('ps_export_enabled', '=', True),
                ('prestashop_instance_id', '=', instance.id),
            ])
            res['total_exported'] = ProductTmpl.search_count([
                ('ps_export_state', '=', 'exported'),
                ('prestashop_instance_id', '=', instance.id),
            ])
            res['total_export_modified'] = ProductTmpl.search_count([
                ('ps_export_state', '=', 'modified'),
                ('prestashop_instance_id', '=', instance.id),
            ])
            res['total_export_errors'] = ProductTmpl.search_count([
                ('ps_export_state', '=', 'error'),
                ('prestashop_instance_id', '=', instance.id),
            ])
            res['total_export_queued'] = self.env['prestashop.export.queue'].search_count([
                ('instance_id', '=', instance.id),
                ('state', '=', 'pending'),
            ])
            res['export_running'] = instance.export_running
            res['last_product_export'] = instance.last_product_export_date
            res['last_stock_export'] = instance.last_stock_export_date
            res['last_price_export'] = instance.last_price_export_date

            # Bidirectional summary
            res['total_imported_from_ps'] = res.get('total_imported', 0)
            res['total_exported_to_ps'] = res.get('total_exported', 0)

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

    def action_clean_and_reimport(self):
        """Reset all imported/updated previews to pending and re-import."""
        instance = self._get_instance()
        previews = self.env['prestashop.product.preview'].search([
            ('instance_id', '=', instance.id),
            ('state', 'in', ('imported', 'updated', 'error')),
        ])
        if not previews:
            raise UserError(_("No products to re-import."))

        # Reset state and clear fallback names
        for preview in previews:
            vals = {'state': 'pending', 'error_message': False}
            # Clear fallback names like "Product PS-xxxx"
            if preview.name and preview.name.startswith('Product PS-'):
                vals['name'] = False
            previews.write(vals)

        # Trigger background import
        instance._import_previews_background(previews.ids)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Clean & Re-import Started'),
                'message': _('%d products reset and queued for re-import.') % len(previews),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_refetch_all_previews(self):
        """Re-fetch full data for all previews that are missing data."""
        instance = self._get_instance()
        previews = self.env['prestashop.product.preview'].search([
            ('instance_id', '=', instance.id),
        ])
        if not previews:
            raise UserError(_("No preview records found."))

        updated = 0
        errors = 0
        for preview in previews:
            try:
                ps_product = instance._fetch_single_product_full(preview.prestashop_id)
                if ps_product and len(ps_product) > 1:
                    preview._update_preview_from_ps_data(ps_product)
                    updated += 1
                else:
                    errors += 1
            except Exception as exc:
                _logger.warning("Re-fetch failed for PS-%s: %s", preview.prestashop_id, exc)
                errors += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Re-fetch Complete'),
                'message': _('%d updated, %d failed.') % (updated, errors),
                'type': 'success' if errors == 0 else 'warning',
                'sticky': True,
            },
        }

    def action_refresh_all_products(self):
        """Re-fetch and update all Odoo products with empty/fallback data."""
        instance = self._get_instance()
        products = self.env['product.template'].search([
            ('prestashop_instance_id', '=', instance.id),
            ('prestashop_id', '!=', False),
        ])
        if not products:
            raise UserError(_("No PrestaShop products found in Odoo."))

        fixed = 0
        errors = 0
        for product in products:
            try:
                ps_product = instance._fetch_single_product_full(product.prestashop_id)
                if ps_product and len(ps_product) > 2:
                    instance._sync_single_product(ps_product)
                    fixed += 1
                else:
                    errors += 1
            except Exception as exc:
                _logger.warning(
                    "Refresh failed for Odoo product %s (PS-%s): %s",
                    product.id, product.prestashop_id, exc,
                )
                errors += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Product Refresh Complete'),
                'message': _('%d products refreshed, %d failed.') % (fixed, errors),
                'type': 'success' if errors == 0 else 'warning',
                'sticky': True,
            },
        }

    def action_open_cleanup_wizard(self):
        instance = self._get_instance()
        wizard = self.env['prestashop.cleanup.wizard'].create({
            'instance_id': instance.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Diagnostic & Cleanup'),
            'res_model': 'prestashop.cleanup.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # =============================================
    # Export Actions
    # =============================================

    def action_export_all_flagged(self):
        """Queue all ps_export_enabled products for export."""
        instance = self._get_instance()
        products = self.env['product.template'].search([
            ('ps_export_enabled', '=', True),
            '|',
            ('prestashop_instance_id', '=', instance.id),
            ('prestashop_instance_id', '=', False),
        ])
        if not products:
            raise UserError(_("No products flagged for export."))
        instance._export_products_background(products.ids)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Export Started'),
                'message': _('%d products queued for export.') % len(products),
                'type': 'info',
                'sticky': True,
            },
        }

    def action_export_modified(self):
        """Queue only products with ps_export_state='modified' for re-export."""
        instance = self._get_instance()
        products = self.env['product.template'].search([
            ('prestashop_instance_id', '=', instance.id),
            ('ps_export_state', '=', 'modified'),
        ])
        if not products:
            raise UserError(_("No modified products to re-export."))
        instance._export_products_background(products.ids)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Re-export Started'),
                'message': _('%d modified products queued for re-export.') % len(products),
                'type': 'info',
                'sticky': True,
            },
        }

    def action_push_stock(self):
        """Trigger immediate stock push for all export-enabled products."""
        instance = self._get_instance()
        products = self.env['product.template'].search([
            ('prestashop_instance_id', '=', instance.id),
            ('ps_export_enabled', '=', True),
            ('prestashop_id', '!=', False),
        ])
        if not products:
            raise UserError(_("No exported products found to push stock."))
        ok = err = 0
        for product in products:
            try:
                instance._push_stock_to_ps(product)
                ok += 1
            except Exception as exc:
                err += 1
                _logger.error("Stock push failed %s: %s", product.name, exc)
        instance.last_stock_export_date = fields.Datetime.now()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Stock Push Complete'),
                'message': _('%d OK, %d errors.') % (ok, err),
                'type': 'success' if not err else 'warning',
                'sticky': True,
            },
        }

    def action_push_prices(self):
        """Trigger immediate price push for all export-enabled products."""
        instance = self._get_instance()
        products = self.env['product.template'].search([
            ('prestashop_instance_id', '=', instance.id),
            ('ps_export_enabled', '=', True),
            ('prestashop_id', '!=', False),
        ])
        if not products:
            raise UserError(_("No exported products found to push prices."))
        ok = err = 0
        for product in products:
            try:
                instance._push_price_to_ps(product)
                ok += 1
            except Exception as exc:
                err += 1
                _logger.error("Price push failed %s: %s", product.name, exc)
        instance.last_price_export_date = fields.Datetime.now()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Price Push Complete'),
                'message': _('%d OK, %d errors.') % (ok, err),
                'type': 'success' if not err else 'warning',
                'sticky': True,
            },
        }

    def action_open_export_queue(self):
        """Open the export queue list view."""
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export Queue'),
            'res_model': 'prestashop.export.queue',
            'view_mode': 'list,form',
            'domain': [('instance_id', '=', instance.id)],
            'target': 'current',
        }

    def action_open_export_log(self):
        """Open the export log list view."""
        instance = self._get_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export History'),
            'res_model': 'prestashop.export.log',
            'view_mode': 'list,form',
            'domain': [('instance_id', '=', instance.id)],
            'target': 'current',
        }

    def action_open_export_wizard(self):
        """Open the export wizard for manual product selection."""
        instance = self._get_instance()
        wizard = self.env['prestashop.export.wizard'].create({
            'instance_id': instance.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export to PrestaShop'),
            'res_model': 'prestashop.export.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _refresh(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('PrestaShop Dashboard'),
            'res_model': 'prestashop.product.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
