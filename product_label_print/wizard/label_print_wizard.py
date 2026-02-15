import math
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductLabelPrintWizard(models.TransientModel):
    _name = 'product.label.print.wizard'
    _description = 'Product Label Print Wizard'

    product_ids = fields.Many2many(
        'product.template',
        string='Products',
        required=True,
    )
    quantity = fields.Integer(
        string='Labels per Product',
        default=1,
        required=True,
    )
    show_promo = fields.Boolean(
        string='Show Promotional Prices',
        default=True,
    )
    show_energy_label = fields.Boolean(
        string='Show Energy Label',
        default=True,
    )
    show_repairability = fields.Boolean(
        string='Show Repairability Index',
        default=True,
    )
    show_deee = fields.Boolean(
        string='Show Eco-tax DEEE',
        default=True,
    )
    max_bullets = fields.Integer(
        string='Max Feature Lines',
        default=5,
    )
    print_mode = fields.Selection(
        selection=[
            ('front_only', 'Front Side Only'),
            ('back_only', 'Back Side Only (Barcodes)'),
            ('both', 'Both Sides (Front then Back)'),
        ],
        string='Print Mode',
        default='both',
        required=True,
    )
    auto_enrich = fields.Boolean(
        string='Auto-enrich before print',
        default=True,
        help='Automatically enrich products with AI that have no features yet.',
    )
    product_count = fields.Integer(
        string='Products Selected',
        compute='_compute_counts',
    )
    total_labels = fields.Integer(
        string='Total Labels',
        compute='_compute_counts',
    )
    total_pages = fields.Integer(
        string='Total A4 Pages',
        compute='_compute_counts',
    )
    products_needing_enrich = fields.Integer(
        string='Need AI Enrichment',
        compute='_compute_counts',
    )

    @api.depends('product_ids', 'quantity')
    def _compute_counts(self):
        for wiz in self:
            count = len(wiz.product_ids)
            total = count * max(wiz.quantity, 1)
            wiz.product_count = count
            wiz.total_labels = total
            wiz.total_pages = math.ceil(total / 2)
            # Count products without AI enrichment
            need_enrich = 0
            for p in wiz.product_ids:
                if not p._get_label_bullet_points(1):
                    need_enrich += 1
            wiz.products_needing_enrich = need_enrich

    @api.constrains('quantity')
    def _check_quantity(self):
        for wiz in self:
            if wiz.quantity < 1:
                raise UserError(_('Quantity must be at least 1.'))

    def action_enrich_products(self):
        """Enrich all selected products with AI."""
        self.ensure_one()
        if not self.product_ids:
            raise UserError(_('Please select at least one product.'))

        enriched = 0
        errors = []
        for product in self.product_ids:
            if hasattr(product, 'action_enrich_with_chatgpt'):
                try:
                    product.action_enrich_with_chatgpt()
                    enriched += 1
                except Exception as e:
                    errors.append(f'{product.name}: {e}')

        msg = _('%d product(s) enriched with AI.', enriched)
        if errors:
            msg += '\n' + _('Errors:') + '\n' + '\n'.join(errors)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('AI Enrichment'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': bool(errors),
                'next': {
                    'type': 'ir.actions.act_window',
                    'res_model': self._name,
                    'res_id': self.id,
                    'view_mode': 'form',
                    'target': 'new',
                },
            },
        }

    def _auto_enrich_if_needed(self):
        """Auto-enrich products that have no bullet points."""
        enriched = 0
        for product in self.product_ids:
            if not product._get_label_bullet_points(1):
                if hasattr(product, 'action_enrich_with_chatgpt'):
                    try:
                        product.action_enrich_with_chatgpt()
                        enriched += 1
                    except Exception as e:
                        _logger.warning('Auto-enrich failed for %s: %s', product.name, e)
        return enriched

    def action_print(self):
        """Generate the PDF report, auto-enriching if enabled."""
        self.ensure_one()
        if not self.product_ids:
            raise UserError(_('Please select at least one product.'))

        # Auto-enrich if enabled
        if self.auto_enrich:
            self._auto_enrich_if_needed()

        data = {
            'product_ids': self.product_ids.ids,
            'quantity': self.quantity,
            'show_promo': self.show_promo,
            'show_energy_label': self.show_energy_label,
            'show_repairability': self.show_repairability,
            'show_deee': self.show_deee,
            'max_bullets': self.max_bullets,
            'print_mode': self.print_mode,
        }

        return self.env.ref(
            'product_label_print.action_report_product_label'
        ).report_action(self.product_ids, data=data)
