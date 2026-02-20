import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class PrestaShopExportQueue(models.Model):
    _name = 'prestashop.export.queue'
    _description = 'PrestaShop Export Queue'
    _order = 'priority desc, create_date asc'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True, ondelete='cascade',
        index=True,
    )
    product_tmpl_id = fields.Many2one(
        'product.template', 'Product', required=True, ondelete='cascade',
    )
    operation = fields.Selection([
        ('create', 'Create in PS'),
        ('update', 'Update in PS'),
        ('price', 'Update Price Only'),
        ('stock', 'Update Stock Only'),
        ('image', 'Update Images'),
        ('variant', 'Sync Variants'),
    ], required=True, string='Operation')
    state = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('done', 'Done'),
        ('error', 'Error'),
        ('cancelled', 'Cancelled'),
    ], default='pending', string='Status', index=True)
    priority = fields.Integer('Priority', default=10)
    retry_count = fields.Integer('Retries', default=0)
    max_retries = fields.Integer('Max Retries', default=3)
    error_message = fields.Text('Error')
    result_ps_id = fields.Char('Created PS ID')
    scheduled_date = fields.Datetime('Scheduled For')
    executed_date = fields.Datetime('Executed At')
    dry_run = fields.Boolean('Dry Run', default=False)
    dry_run_xml = fields.Text('Dry Run XML Preview')
    export_data_preview = fields.Text('Data Preview (JSON)')

    def action_queue_retry(self):
        """Reset failed queue items to pending for retry."""
        failed = self.filtered(lambda q: q.state == 'error')
        failed.write({
            'state': 'pending',
            'retry_count': 0,
            'error_message': False,
        })

    def action_queue_cancel(self):
        """Cancel pending queue items."""
        pending = self.filtered(lambda q: q.state in ('pending', 'error'))
        pending.write({'state': 'cancelled'})

    def action_queue_process_now(self):
        """Process a single queue item immediately."""
        self.ensure_one()
        if self.state not in ('pending', 'error'):
            return
        instance = self.instance_id
        product = self.product_tmpl_id
        self.state = 'processing'
        try:
            if self.operation in ('create', 'update'):
                result = instance._export_single_product(product)
            elif self.operation == 'price':
                instance._push_price_to_ps(product)
                result = {'success': True}
            elif self.operation == 'stock':
                instance._push_stock_to_ps(product)
                result = {'success': True}
            elif self.operation == 'variant':
                if product.prestashop_id:
                    instance._export_product_variants(product, product.prestashop_id)
                result = {'success': True}
            else:
                result = {'success': False, 'error': 'Unknown operation'}

            if result.get('success'):
                self.write({
                    'state': 'done',
                    'executed_date': fields.Datetime.now(),
                    'result_ps_id': result.get('ps_id', ''),
                })
            else:
                self.write({
                    'state': 'error',
                    'error_message': result.get('error', 'Unknown error'),
                    'retry_count': self.retry_count + 1,
                })
        except Exception as exc:
            self.write({
                'state': 'error',
                'error_message': str(exc)[:500],
                'retry_count': self.retry_count + 1,
            })
