import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopCleanupWizard(models.TransientModel):
    _name = 'prestashop.cleanup.wizard'
    _description = 'PrestaShop Diagnostic & Cleanup Wizard'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True,
    )
    only_active = fields.Boolean(
        'Only active products (in PS)', default=True,
    )

    # Results
    test_results = fields.Text('Diagnostic Results', readonly=True)
    progress_log = fields.Text('Progress Log', readonly=True)
    products_fixed = fields.Integer('Products Fixed', readonly=True)
    products_failed = fields.Integer('Products Failed', readonly=True)

    state = fields.Selection([
        ('draft', 'Ready'),
        ('tested', 'Tested'),
        ('running', 'Running'),
        ('done', 'Done'),
    ], default='draft')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        instance = self.env['prestashop.instance'].search([
            ('active', '=', True),
        ], limit=1)
        if instance:
            res['instance_id'] = instance.id
        return res

    # ------------------------------------------------------------------
    # DIAGNOSTIC: Test 5 products with 4 strategies
    # ------------------------------------------------------------------
    def action_test_5(self):
        """Test 4 different API fetch strategies on 5 sample products."""
        self.ensure_one()
        instance = self.instance_id
        if not instance:
            raise UserError(_("No instance selected."))

        # Pick 5 preview records
        domain = [('instance_id', '=', instance.id), ('state', '!=', 'skipped')]
        if self.only_active:
            domain.append(('active_in_ps', '=', True))
        previews = self.env['prestashop.product.preview'].search(
            domain, limit=5, order='id',
        )
        if not previews:
            raise UserError(_("No preview products found."))

        lines = []
        lines.append("=" * 60)
        lines.append("DIAGNOSTIC: Testing 4 API strategies on %d products" % len(previews))
        lines.append("Instance: %s" % instance.url)
        lines.append("=" * 60)

        ps_fields = instance._PS_PRODUCT_FIELDS

        for preview in previews:
            ps_id = preview.prestashop_id
            lines.append("")
            lines.append("--- PS-%s (%s) ---" % (ps_id, preview.name or '?'))

            # Strategy A: List endpoint + filter + explicit fields (NO associations)
            lines.append(self._test_strategy(
                instance, "A: List + filter + fields",
                resource=None, params={
                    'display': '[%s]' % ps_fields,
                    'filter[id]': str(ps_id),
                },
            ))

            # Strategy B: List endpoint + filter + display=full
            lines.append(self._test_strategy(
                instance, "B: List + filter + full",
                resource=None, params={
                    'display': 'full',
                    'filter[id]': str(ps_id),
                },
            ))

            # Strategy C: Single resource + explicit fields
            lines.append(self._test_strategy(
                instance, "C: Single + fields",
                resource=ps_id, params={
                    'display': '[%s]' % ps_fields,
                },
            ))

            # Strategy D: Single resource + display=full
            lines.append(self._test_strategy(
                instance, "D: Single + full",
                resource=ps_id, params={
                    'display': 'full',
                },
            ))

        lines.append("")
        lines.append("=" * 60)
        lines.append("DONE. Use the strategy that returns the most keys.")

        self.write({
            'test_results': '\n'.join(lines),
            'state': 'tested',
        })
        return self._reopen()

    def _test_strategy(self, instance, label, resource, params):
        """Test a single API strategy and return a result line."""
        try:
            if resource:
                data = instance._api_get_long(
                    'products', resource_id=str(resource),
                    params=params, timeout=30,
                )
            else:
                data = instance._api_get_long(
                    'products', params=params, timeout=30,
                )

            product = instance._extract_product_from_response(data)
            key_count = len(product) if product else 0
            keys = list(product.keys())[:8] if product else []
            name = ''
            if product:
                raw_name = product.get('name', '')
                name = instance._get_ps_text(raw_name)

            if key_count > 2:
                return "  [OK] %s => %d keys, name=%r, keys=%s" % (
                    label, key_count, name[:40] if name else '', keys,
                )
            else:
                return "  [FAIL] %s => %d keys only: %s" % (label, key_count, keys)

        except Exception as exc:
            return "  [ERROR] %s => %s" % (label, str(exc)[:100])

    # ------------------------------------------------------------------
    # CLEANUP: Re-fetch all previews
    # ------------------------------------------------------------------
    def action_clean_previews(self):
        """Re-fetch full data for all previews using the working strategy."""
        self.ensure_one()
        instance = self.instance_id
        if not instance:
            raise UserError(_("No instance selected."))

        domain = [('instance_id', '=', instance.id)]
        if self.only_active:
            domain.append(('active_in_ps', '=', True))
        previews = self.env['prestashop.product.preview'].search(domain)

        if not previews:
            raise UserError(_("No preview records found."))

        self.write({'state': 'running', 'progress_log': 'Starting...\n'})

        fixed = failed = 0
        log_lines = []

        for idx, preview in enumerate(previews, 1):
            try:
                ps_product = instance._fetch_single_product_full(preview.prestashop_id)
                if ps_product and len(ps_product) > 2:
                    preview._update_preview_from_ps_data(ps_product)
                    fixed += 1
                    if idx <= 20 or idx % 50 == 0:
                        log_lines.append(
                            "[%d/%d] PS-%s OK: %s" % (
                                idx, len(previews), preview.prestashop_id,
                                preview.name or '?',
                            )
                        )
                else:
                    failed += 1
                    log_lines.append(
                        "[%d/%d] PS-%s FAIL: only %d keys" % (
                            idx, len(previews), preview.prestashop_id,
                            len(ps_product) if ps_product else 0,
                        )
                    )
            except Exception as exc:
                failed += 1
                log_lines.append(
                    "[%d/%d] PS-%s ERROR: %s" % (
                        idx, len(previews), preview.prestashop_id,
                        str(exc)[:80],
                    )
                )

            # Commit every 20 products
            if idx % 20 == 0:
                self.env.cr.commit()

        self.env.cr.commit()
        log_lines.append("")
        log_lines.append("DONE: %d fixed, %d failed out of %d" % (fixed, failed, len(previews)))

        self.write({
            'state': 'done',
            'progress_log': '\n'.join(log_lines),
            'products_fixed': fixed,
            'products_failed': failed,
        })
        return self._reopen()

    # ------------------------------------------------------------------
    # CLEANUP: Re-sync all Odoo products
    # ------------------------------------------------------------------
    def action_clean_products(self):
        """Re-fetch and re-sync all Odoo products from PrestaShop."""
        self.ensure_one()
        instance = self.instance_id
        if not instance:
            raise UserError(_("No instance selected."))

        products = self.env['product.template'].search([
            ('prestashop_instance_id', '=', instance.id),
            ('prestashop_id', '!=', False),
        ])
        if not products:
            raise UserError(_("No PrestaShop products in Odoo."))

        self.write({'state': 'running', 'progress_log': 'Starting...\n'})

        fixed = failed = 0
        log_lines = []

        for idx, product in enumerate(products, 1):
            ps_id = product.prestashop_id
            try:
                ps_product = instance._fetch_single_product_full(ps_id)
                if ps_product and len(ps_product) > 2:
                    instance._sync_single_product(ps_product)
                    fixed += 1
                    if idx <= 20 or idx % 50 == 0:
                        log_lines.append(
                            "[%d/%d] PS-%s OK: %s" % (
                                idx, len(products), ps_id,
                                product.name or '?',
                            )
                        )
                else:
                    failed += 1
                    log_lines.append(
                        "[%d/%d] PS-%s FAIL: empty response" % (
                            idx, len(products), ps_id,
                        )
                    )
            except Exception as exc:
                failed += 1
                log_lines.append(
                    "[%d/%d] PS-%s ERROR: %s" % (
                        idx, len(products), ps_id,
                        str(exc)[:80],
                    )
                )

            if idx % 20 == 0:
                self.env.cr.commit()

        self.env.cr.commit()
        log_lines.append("")
        log_lines.append("DONE: %d fixed, %d failed out of %d" % (fixed, failed, len(products)))

        self.write({
            'state': 'done',
            'progress_log': '\n'.join(log_lines),
            'products_fixed': fixed,
            'products_failed': failed,
        })
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Diagnostic & Cleanup'),
            'res_model': 'prestashop.cleanup.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
