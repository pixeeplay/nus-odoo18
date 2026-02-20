import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrestaShopExportWizard(models.TransientModel):
    _name = 'prestashop.export.wizard'
    _description = 'PrestaShop Product Export Wizard'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True,
    )
    product_ids = fields.Many2many(
        'product.template', string='Products to Export',
    )
    product_count = fields.Integer(
        'Total Products', compute='_compute_counts',
    )
    create_count = fields.Integer(
        'Will Create', compute='_compute_counts',
    )
    update_count = fields.Integer(
        'Will Update', compute='_compute_counts',
    )

    export_mode = fields.Selection([
        ('full', 'Full Export (all fields)'),
        ('price_only', 'Price Only'),
        ('stock_only', 'Stock Only'),
        ('price_and_stock', 'Price + Stock'),
    ], default='full', string='Export Mode')

    dry_run = fields.Boolean('Preview Only (Dry Run)', default=False)
    include_variants = fields.Boolean('Include Variants', default=True)
    include_images = fields.Boolean('Include Images', default=True)

    state = fields.Selection([
        ('draft', 'Ready'),
        ('validating', 'Validating...'),
        ('preview', 'Preview'),
        ('running', 'Running'),
        ('done', 'Done'),
    ], default='draft')

    validation_log = fields.Html('Validation Results')
    export_log = fields.Html('Export Results')

    # Stats
    total = fields.Integer('Total')
    created = fields.Integer('Created')
    updated = fields.Integer('Updated')
    errors = fields.Integer('Errors')

    @api.depends('product_ids')
    def _compute_counts(self):
        for wiz in self:
            products = wiz.product_ids
            wiz.product_count = len(products)
            wiz.update_count = len(products.filtered(lambda p: p.prestashop_id))
            wiz.create_count = wiz.product_count - wiz.update_count

    def action_validate(self):
        """Validate all products before export."""
        self.ensure_one()
        instance = self.instance_id
        log_lines = []
        has_errors = False

        for product in self.product_ids:
            errors = instance._validate_product_for_export(product)
            if errors:
                has_errors = True
                log_lines.append(
                    '<p style="color:red;"><strong>%s</strong>: %s</p>'
                    % (product.name, '<br/>'.join(errors))
                )
            else:
                log_lines.append(
                    '<p style="color:green;"><strong>%s</strong>: OK</p>'
                    % product.name
                )

        self.validation_log = ''.join(log_lines)
        self.state = 'preview' if not has_errors else 'validating'
        return self._reopen()

    def action_start_export(self):
        """Start the export process."""
        self.ensure_one()
        instance = self.instance_id
        products = self.product_ids

        if not products:
            raise UserError(_("No products selected for export."))

        self.state = 'running'
        self.total = len(products)

        if self.export_mode == 'full':
            if self.dry_run:
                # Dry run: generate XML preview
                log_lines = []
                for product in products:
                    result = instance._export_single_product(product, dry_run=True)
                    xml_preview = (result.get('xml') or '')[:2000]
                    op = result.get('operation', '?')
                    log_lines.append(
                        '<p><strong>%s</strong> [%s]</p>'
                        '<pre style="max-height:200px;overflow:auto;">%s</pre>'
                        % (product.name, op, xml_preview)
                    )
                self.export_log = ''.join(log_lines)
                self.state = 'done'
                return self._reopen()
            else:
                # Real export in background
                instance._export_products_background(products.ids)
                self.state = 'done'
                self.export_log = (
                    '<p>%d products queued for background export. '
                    'You will receive notifications as each product is processed.</p>'
                ) % len(products)
                return self._reopen()
        elif self.export_mode == 'price_only':
            ok = err = 0
            for product in products:
                try:
                    instance._push_price_to_ps(product)
                    ok += 1
                except Exception as exc:
                    err += 1
                    _logger.error("Price push failed %s: %s", product.name, exc)
            self.export_log = '<p>Price update: %d OK, %d errors.</p>' % (ok, err)
            self.state = 'done'
            instance.last_price_export_date = fields.Datetime.now()
            return self._reopen()
        elif self.export_mode == 'stock_only':
            ok = err = 0
            for product in products:
                try:
                    instance._push_stock_to_ps(product)
                    ok += 1
                except Exception as exc:
                    err += 1
                    _logger.error("Stock push failed %s: %s", product.name, exc)
            self.export_log = '<p>Stock update: %d OK, %d errors.</p>' % (ok, err)
            self.state = 'done'
            instance.last_stock_export_date = fields.Datetime.now()
            return self._reopen()
        elif self.export_mode == 'price_and_stock':
            ok = err = 0
            for product in products:
                try:
                    instance._push_price_to_ps(product)
                    instance._push_stock_to_ps(product)
                    ok += 1
                except Exception as exc:
                    err += 1
                    _logger.error("Price+Stock push failed %s: %s", product.name, exc)
            self.export_log = '<p>Price + Stock update: %d OK, %d errors.</p>' % (ok, err)
            self.state = 'done'
            instance.last_price_export_date = fields.Datetime.now()
            instance.last_stock_export_date = fields.Datetime.now()
            return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export to PrestaShop'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
