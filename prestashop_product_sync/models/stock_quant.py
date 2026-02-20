import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def write(self, vals):
        res = super().write(vals)
        if 'quantity' in vals or 'reserved_quantity' in vals:
            self._trigger_ps_stock_push()
        return res

    def _trigger_ps_stock_push(self):
        """Push stock to PrestaShop in real-time when quant quantities change.

        Only active if the instance has stock_realtime_push_date set
        and today >= that date. Otherwise, stock is only pushed via cron.
        """
        product_ids = self.mapped('product_id')
        if not product_ids:
            return

        templates = product_ids.mapped('product_tmpl_id').filtered(
            lambda t: t.ps_export_enabled
                      and t.prestashop_id
                      and t.prestashop_instance_id
        )
        if not templates:
            return

        today = fields.Date.today()
        for tmpl in templates:
            instance = tmpl.prestashop_instance_id
            if not instance or not instance.active:
                continue
            if instance.stock_sync_mode == 'disabled':
                continue
            # Check activation date — skip if not yet active
            if not instance.stock_realtime_push_date:
                continue
            if today < instance.stock_realtime_push_date:
                continue
            try:
                instance._push_stock_to_ps(tmpl)
                _logger.info(
                    "Real-time stock push for %s (PS-%s)",
                    tmpl.name, tmpl.prestashop_id,
                )
            except Exception as exc:
                _logger.error(
                    "Real-time stock push failed for %s: %s",
                    tmpl.name, exc,
                )
