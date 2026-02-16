# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BatchEnrichmentWizard(models.TransientModel):
    _name = 'batch.enrichment.wizard'
    _description = 'AI Batch Enrichment Wizard'

    product_ids = fields.Many2many(
        'product.template', string='Products to Enrich')
    product_count = fields.Integer(
        compute='_compute_counts', string='Selected Products')
    skip_already_enriched = fields.Boolean(
        string='Skip Already Enriched', default=True,
        help="Ignore products that already have AI enrichment data.")
    skip_already_queued = fields.Boolean(
        string='Skip Already in Queue', default=True,
        help="Ignore products already in the enrichment queue.")
    priority = fields.Selection([
        ('0', 'Basse'),
        ('1', 'Normale'),
        ('2', 'Haute'),
    ], string='Priority', default='1')
    estimated_time = fields.Char(
        compute='_compute_counts', string='Estimated Time')

    @api.depends('product_ids', 'skip_already_enriched', 'skip_already_queued')
    def _compute_counts(self):
        for rec in self:
            count = len(rec.product_ids)
            rec.product_count = count
            # ~30s per product (3s SearXNG + 20s Ollama + margin)
            total_seconds = count * 30
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if hours:
                rec.estimated_time = _("%d produits ≈ %dh %dmin") % (count, hours, minutes)
            else:
                rec.estimated_time = _("%d produits ≈ %dmin") % (count, minutes)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            res['product_ids'] = [(6, 0, active_ids)]
        return res

    def action_enqueue(self):
        """Create queue entries for selected products."""
        self.ensure_one()
        if not self.product_ids:
            raise UserError(_("No products selected."))

        Queue = self.env['product.enrichment.queue']
        created = 0
        skipped_enriched = 0
        skipped_queued = 0

        for product in self.product_ids:
            if not product.name:
                continue

            # Skip already enriched
            if self.skip_already_enriched and product.ai_enrichment_date:
                skipped_enriched += 1
                continue

            # Check if already in queue
            existing = Queue.search([
                ('product_id', '=', product.id),
                ('state', 'not in', ['done', 'error', 'skipped']),
            ], limit=1)
            if self.skip_already_queued and existing:
                skipped_queued += 1
                continue

            if not existing:
                Queue.create({
                    'product_id': product.id,
                    'priority': self.priority,
                })
                created += 1

        msg_parts = [_('%d produit(s) ajouté(s) à la file d\'attente.') % created]
        if skipped_enriched:
            msg_parts.append(_('%d ignoré(s) (déjà enrichi).') % skipped_enriched)
        if skipped_queued:
            msg_parts.append(_('%d ignoré(s) (déjà en file).') % skipped_queued)

        _logger.info("Batch enqueue: %d created, %d skipped (enriched), %d skipped (queued)",
                      created, skipped_enriched, skipped_queued)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Enrichissement batch'),
                'message': ' '.join(msg_parts),
                'type': 'success',
                'sticky': True,
            },
        }
