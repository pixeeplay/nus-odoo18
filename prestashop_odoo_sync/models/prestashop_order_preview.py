# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopOrderPreview(models.Model):
    _name = 'prestashop.order.preview'
    _description = 'PrestaShop Order Preview'
    _order = 'prestashop_order_date desc'

    # Header Information
    name = fields.Char(string='Order Reference', required=True, readonly=True)
    instance_id = fields.Many2one('prestashop.instance', string='Instance', required=True, readonly=True, ondelete='cascade')
    prestashop_order_id = fields.Char(string='PrestaShop ID', required=True, readonly=True, index=True)
    prestashop_order_date = fields.Datetime(string='Order Date', readonly=True)

    # Customer Information
    customer_name = fields.Char(string='Customer', readonly=True)
    customer_email = fields.Char(string='Email', readonly=True)
    prestashop_customer_id = fields.Char(string='PrestaShop Customer ID', readonly=True)

    # Order Details
    total_amount = fields.Float(string='Total Amount', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)
    payment_method = fields.Char(string='Payment Method', readonly=True)
    order_state = fields.Char(string='Order Status', readonly=True)

    # Import Status
    state = fields.Selection([
        ('pending', 'Pending Review'),
        ('imported', 'Imported'),
        ('error', 'Import Error'),
        ('skipped', 'Skipped')
    ], default='pending', string='Status', readonly=True)

    imported_order_id = fields.Many2one('sale.order', string='Imported Order', readonly=True)
    import_date = fields.Datetime(string='Import Date', readonly=True)
    error_message = fields.Text(string='Error Message', readonly=True)

    # Raw Data Storage
    raw_data = fields.Text(string='Raw XML Data', readonly=True)
    line_ids = fields.One2many('prestashop.order.preview.line', 'preview_id', string='Order Lines', readonly=True)

    _sql_constraints = [
        ('unique_order_instance', 'unique(prestashop_order_id, instance_id)',
         'This PrestaShop order has already been fetched for this instance!')
    ]

    def action_import_order(self):
        """Import single order into Odoo"""
        self.ensure_one()
        if self.state == 'imported':
            raise UserError(_("This order has already been imported!"))

        return self._import_to_odoo()

    def action_skip_order(self):
        """Mark order as skipped"""
        self.write({'state': 'skipped'})

    def action_view_imported_order(self):
        """Navigate to imported sale order"""
        self.ensure_one()
        if not self.imported_order_id:
            raise UserError(_("No imported order found!"))

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': self.imported_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _import_to_odoo(self):
        """Core import logic extracted from prestashop_instance.action_sync_now()"""
        self.ensure_one()
        instance = self.instance_id

        try:
            # 1. Find or create customer
            partner = instance._find_or_create_customer(self.prestashop_customer_id)
            if not partner:
                raise UserError(_("Failed to sync customer %s") % self.customer_email)

            # 2. Create Sale Order
            so_vals = {
                'partner_id': partner.id,
                'prestashop_instance_id': instance.id,
                'prestashop_order_id': self.prestashop_order_id,
                'prestashop_source': 'prestashop',
                'warehouse_id': instance.warehouse_id.id,
                'company_id': instance.company_id.id,
                'origin': f"PS#{self.prestashop_order_id}",
                'date_order': self.prestashop_order_date,
            }
            sale_order = self.env['sale.order'].create(so_vals)

            # 3. Create order lines
            for preview_line in self.line_ids:
                product = instance._find_or_create_product(preview_line.prestashop_product_id)
                if not product:
                    _logger.warning("Could not sync product %s", preview_line.product_name)
                    continue

                self.env['sale.order.line'].create({
                    'order_id': sale_order.id,
                    'product_id': product.id,
                    'product_uom_qty': preview_line.quantity,
                    'price_unit': preview_line.unit_price,
                    'name': preview_line.product_name,
                })

            # 4. Update preview record
            self.write({
                'state': 'imported',
                'imported_order_id': sale_order.id,
                'import_date': fields.Datetime.now(),
            })

            # 5. Log success
            self.env['prestashop.sync.log'].create({
                'instance_id': instance.id,
                'name': f"Order {self.prestashop_order_id}",
                'status': 'success',
                'message': f"Successfully imported order to SO/{sale_order.name}",
            })

            _logger.info("Successfully imported PrestaShop Order %s -> %s",
                        self.prestashop_order_id, sale_order.name)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Order imported successfully as %s') % sale_order.name,
                    'type': 'success',
                    'sticky': False,
                    'next': {
                        'type': 'ir.actions.act_window',
                        'res_model': 'sale.order',
                        'res_id': sale_order.id,
                        'view_mode': 'form',
                    },
                }
            }

        except Exception as e:
            error_msg = str(e)
            _logger.error("Failed to import PrestaShop Order %s: %s",
                         self.prestashop_order_id, error_msg)

            self.write({
                'state': 'error',
                'error_message': error_msg,
            })

            self.env['prestashop.sync.log'].create({
                'instance_id': instance.id,
                'name': f"Order {self.prestashop_order_id}",
                'status': 'error',
                'message': error_msg,
            })

            raise UserError(_("Import failed: %s") % error_msg)


class PrestaShopOrderPreviewLine(models.Model):
    _name = 'prestashop.order.preview.line'
    _description = 'PrestaShop Order Preview Line'

    preview_id = fields.Many2one('prestashop.order.preview', string='Preview Order',
                                  required=True, readonly=True, ondelete='cascade')

    prestashop_product_id = fields.Char(string='PrestaShop Product ID', readonly=True)
    product_name = fields.Char(string='Product', readonly=True)
    product_reference = fields.Char(string='Reference/SKU', readonly=True)
    quantity = fields.Float(string='Quantity', readonly=True)
    unit_price = fields.Float(string='Unit Price', readonly=True)
    total_price = fields.Float(string='Total', readonly=True)
