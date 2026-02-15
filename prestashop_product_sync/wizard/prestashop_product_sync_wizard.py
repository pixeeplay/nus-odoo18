import logging
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopProductSyncWizard(models.TransientModel):
    _name = 'prestashop.product.sync.wizard'
    _description = 'PrestaShop Product Sync Wizard'

    instance_id = fields.Many2one(
        'prestashop.instance', string='Instance', required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    product_limit = fields.Integer('Products to sync', default=5)
    state = fields.Selection([
        ('draft', 'Ready'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft', readonly=True)
    progress = fields.Float('Progress', readonly=True)
    total = fields.Integer('Total', readonly=True)
    current = fields.Integer('Current', readonly=True)
    created_count = fields.Integer('Created', readonly=True)
    updated_count = fields.Integer('Updated', readonly=True)
    error_count = fields.Integer('Errors', readonly=True)
    log = fields.Html('Sync Log', readonly=True)

    def _send_progress(self, title, message, notif_type='info'):
        """Send a real-time bus notification to the current user."""
        self.env['bus.bus']._sendone(
            self.env.user.partner_id,
            'simple_notification',
            {
                'title': title,
                'message': message,
                'type': notif_type,
                'sticky': False,
            },
        )

    def _append_log(self, icon, text, style=''):
        """Append a line to the HTML log."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        line = (
            f'<div style="padding:2px 0;{style}">'
            f'<span style="color:#888;">[{timestamp}]</span> '
            f'{icon} {text}</div>'
        )
        self.log = (self.log or '') + line

    def action_start_sync(self):
        """Run the product sync with live progress notifications."""
        self.ensure_one()
        instance = self.instance_id
        if not instance:
            raise UserError(_("Please select a PrestaShop instance."))

        self.write({
            'state': 'running',
            'progress': 0,
            'log': '',
            'created_count': 0,
            'updated_count': 0,
            'error_count': 0,
        })
        self.env.cr.commit()

        # --- Step 1: Fetch product IDs ---
        self._append_log('🔍', f'<b>Fetching active product IDs from {instance.name}...</b>')
        self._send_progress('Sync Started', f'Fetching product list from {instance.name}...')
        self.env.cr.commit()

        try:
            all_ids = instance._fetch_product_ids()
        except Exception as exc:
            self._append_log('❌', f'<span style="color:red;">Failed to fetch product list: {exc}</span>')
            self.write({'state': 'error'})
            self.env.cr.commit()
            return self._return_wizard()

        product_ids = all_ids[:self.product_limit] if self.product_limit else all_ids
        total = len(product_ids)
        self.write({'total': total})

        self._append_log(
            '📋',
            f'Found <b>{len(all_ids)}</b> active products. '
            f'Syncing <b>{total}</b>.',
        )
        self._send_progress('Products Found', f'{len(all_ids)} active products, syncing {total}')
        self.env.cr.commit()

        if not product_ids:
            self._append_log('⚠️', 'No products to sync.')
            self.write({'state': 'done'})
            self.env.cr.commit()
            return self._return_wizard()

        # --- Step 2: Sync each product ---
        created = updated = errors = 0

        for idx, ps_id in enumerate(product_ids, 1):
            pct = round((idx / total) * 100, 1)

            try:
                # Check if product already exists
                already = self.env['product.template'].search([
                    ('prestashop_id', '=', ps_id),
                    ('prestashop_instance_id', '=', instance.id),
                ], limit=1)

                # Send live progress
                self._send_progress(
                    f'Sync {idx}/{total} ({pct}%)',
                    f'Loading product PS-{ps_id}...',
                )

                self._append_log(
                    '⏳',
                    f'<b>[{idx}/{total}]</b> Loading product PS-{ps_id}...',
                    'color:#555;',
                )
                self.write({'current': idx, 'progress': pct})
                self.env.cr.commit()

                # Fetch full product data
                ps_product = instance._fetch_single_product_full(ps_id)
                if not ps_product:
                    self._append_log('⚠️', f'Product PS-{ps_id}: empty response, skipped.')
                    errors += 1
                    continue

                product_name = instance._get_ps_text(ps_product.get('name', ''))
                reference = ps_product.get('reference', '') or ''

                # Sync to Odoo
                instance._sync_single_product(ps_product)

                if already:
                    updated += 1
                    self._append_log(
                        '🔄',
                        f'<span style="color:#2196F3;">'
                        f'<b>{product_name}</b> [{reference}] — updated</span>',
                    )
                    self._send_progress(
                        f'Updated {idx}/{total}',
                        f'{product_name}',
                        'info',
                    )
                else:
                    created += 1
                    self._append_log(
                        '✅',
                        f'<span style="color:#4CAF50;">'
                        f'<b>{product_name}</b> [{reference}] — created</span>',
                    )
                    self._send_progress(
                        f'Created {idx}/{total}',
                        f'{product_name}',
                        'success',
                    )

            except Exception as exc:
                errors += 1
                self._append_log(
                    '❌',
                    f'<span style="color:red;">'
                    f'Product PS-{ps_id}: {exc}</span>',
                )
                self._send_progress(
                    f'Error {idx}/{total}',
                    f'PS-{ps_id}: {exc}',
                    'danger',
                )
                _logger.error("Sync wizard error on PS-%s: %s", ps_id, exc)

            # Persist progress after each product
            self.write({
                'progress': pct,
                'current': idx,
                'created_count': created,
                'updated_count': updated,
                'error_count': errors,
            })
            self.env.cr.commit()

        # --- Step 3: Done ---
        self._append_log(
            '🏁',
            f'<b style="font-size:14px;">Sync complete — '
            f'Created: {created} | Updated: {updated} | Errors: {errors}</b>',
        )
        self.write({
            'state': 'done',
            'progress': 100,
        })
        instance.last_product_sync_date = fields.Datetime.now()
        self.env.cr.commit()

        self._send_progress(
            'Sync Complete!',
            f'Created: {created} | Updated: {updated} | Errors: {errors}',
            'success' if not errors else 'warning',
        )

        return self._return_wizard()

    def _return_wizard(self):
        """Return the wizard form so the user sees the final state."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Product Sync'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
