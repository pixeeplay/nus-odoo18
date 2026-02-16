# -*- coding: utf-8 -*-
import json
import time
import re
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Mapping: Ollama JSON key → product.template ai_* field
AI_FIELD_MAPPING = {
    'titre_seo': 'ai_seo_title',
    'meta_description': 'ai_meta_description',
    'description_courte': 'ai_short_description',
    'description_longue_html': 'ai_description_html',
    'categorie_suggeree': 'ai_suggested_category',
    'marque_detectee': 'ai_detected_brand',
    'public_cible': 'ai_target_audience',
    'confiance': 'ai_confidence',
}

# Mapping: ai_* field → Odoo standard field (for auto-publish)
STANDARD_FIELD_MAPPING = {
    'ai_description_html': 'website_description',
    'ai_short_description': 'description_sale',
    'ai_seo_title': 'website_meta_title',
    'ai_meta_description': 'website_meta_description',
}


class ProductEnrichmentQueue(models.Model):
    _name = 'product.enrichment.queue'
    _description = 'AI Product Enrichment Queue'
    _order = 'priority desc, date_queued asc'
    _rec_name = 'product_id'

    product_id = fields.Many2one(
        'product.template', string='Product',
        required=True, ondelete='cascade', index=True)
    state = fields.Selection([
        ('pending', 'En attente'),
        ('collecting', 'Collecte en cours'),
        ('collected', 'Collecté'),
        ('enriching', 'Enrichissement en cours'),
        ('done', 'Terminé'),
        ('error', 'Erreur'),
        ('skipped', 'Ignoré'),
    ], string='State', default='pending', required=True, index=True)
    priority = fields.Selection([
        ('0', 'Basse'),
        ('1', 'Normale'),
        ('2', 'Haute'),
    ], string='Priority', default='1')

    # SearXNG data
    search_query_used = fields.Char(string='Search Query')
    raw_web_data = fields.Text(string='Raw Web Data (JSON)')

    # Ollama data
    raw_ollama_response = fields.Text(string='Raw Ollama Response')
    enriched_data = fields.Text(string='Enriched Data (JSON)')

    # Error tracking
    error_message = fields.Text(string='Error Message')
    attempt_count = fields.Integer(string='Attempts', default=0)
    max_attempts = fields.Integer(string='Max Attempts', default=3)

    # Timing
    processing_time_search = fields.Float(string='Search Time (s)')
    processing_time_ollama = fields.Float(string='Ollama Time (s)')

    # Dates
    date_queued = fields.Datetime(string='Queued', default=fields.Datetime.now)
    date_collected = fields.Datetime(string='Collected')
    date_enriched = fields.Datetime(string='Enriched')

    # -------------------------------------------------------
    # Duplicate prevention
    # -------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            product_id = vals.get('product_id')
            if product_id:
                existing = self.search([
                    ('product_id', '=', product_id),
                    ('state', 'not in', ['done', 'error', 'skipped']),
                ], limit=1)
                if existing:
                    raise UserError(
                        _("Product '%s' already has an active enrichment in queue (state: %s).")
                        % (existing.product_id.name, existing.state))
        return super().create(vals_list)

    # -------------------------------------------------------
    # Cron 1: SearXNG Web Data Collection
    # -------------------------------------------------------
    @api.model
    def _cron_collect_web_data(self):
        """Process pending queue items via SearXNG."""
        config = self.env['chatgpt.config'].get_active_config()
        if not config.searxng_enabled:
            _logger.info("AI Queue Collect: SearXNG is disabled, skipping.")
            return

        batch_size = config.enrichment_batch_size_collect or 20
        items = self.search([
            ('state', '=', 'pending'),
        ], limit=batch_size, order='priority desc, date_queued asc')

        if not items:
            _logger.info("AI Queue Collect: nothing to process.")
            return

        client = config._get_searxng_client()
        _logger.info("AI Queue Collect: processing %d items...", len(items))

        for item in items:
            try:
                item.write({'state': 'collecting'})
                self.env.cr.commit()

                product = item.product_id
                t0 = time.time()

                # Build search context
                ean = ''
                if hasattr(product, 'barcode') and product.barcode:
                    ean = product.barcode
                brand = ''
                if hasattr(product, 'product_brand_id') and product.product_brand_id:
                    brand = product.product_brand_id.name

                result = client.search_product(
                    product_name=product.name,
                    ean=ean,
                    brand=brand,
                )
                elapsed = time.time() - t0

                item.write({
                    'search_query_used': result.get('query_tech', ''),
                    'raw_web_data': json.dumps(result.get('results', []),
                                               ensure_ascii=False, indent=2),
                    'processing_time_search': round(elapsed, 2),
                    'date_collected': fields.Datetime.now(),
                    'state': 'collected',
                    'error_message': False,
                })
                self.env.cr.commit()
                _logger.info("AI Collect OK: %s (%d results, %.1fs)",
                             product.name, len(result.get('results', [])), elapsed)

            except Exception as e:
                self.env.cr.rollback()
                item = item.exists()
                if item:
                    attempt = item.attempt_count + 1
                    new_state = 'skipped' if attempt >= item.max_attempts else 'error'
                    item.write({
                        'state': new_state,
                        'error_message': str(e)[:2000],
                        'attempt_count': attempt,
                    })
                    self.env.cr.commit()
                _logger.error("AI Collect FAIL: %s: %s",
                              item.product_id.name if item.exists() else '?', str(e))

    # -------------------------------------------------------
    # Cron 2: Ollama AI Enrichment
    # -------------------------------------------------------
    @api.model
    def _cron_enrich_ollama(self):
        """Process collected queue items via Ollama."""
        config = self.env['chatgpt.config'].get_active_config()
        batch_size = config.enrichment_batch_size_enrich or 10
        items = self.search([
            ('state', '=', 'collected'),
        ], limit=batch_size, order='priority desc, date_collected asc')

        if not items:
            _logger.info("AI Queue Enrich: nothing to process.")
            return

        _logger.info("AI Queue Enrich: processing %d items...", len(items))
        prompt_template = config.enrichment_prompt_template or ''

        for item in items:
            try:
                item.write({'state': 'enriching'})
                self.env.cr.commit()

                product = item.product_id
                t0 = time.time()

                # Build prompt context from web data
                web_results = json.loads(item.raw_web_data or '[]')
                web_context_parts = []
                for r in web_results[:10]:
                    title = r.get('title', '')
                    content = r.get('content', '')[:500]
                    url = r.get('url', '')
                    web_context_parts.append(f"[{title}]({url}): {content}")
                web_context = "\n\n".join(web_context_parts) or "Aucune donnée web disponible."

                # Build product context
                ean = getattr(product, 'barcode', '') or ''
                brand = ''
                if hasattr(product, 'product_brand_id') and product.product_brand_id:
                    brand = product.product_brand_id.name
                categ_name = product.categ_id.complete_name if product.categ_id else ''
                current_desc = ''
                if product.description_sale:
                    current_desc = product.description_sale[:300]

                # Format prompt
                prompt = prompt_template.format(
                    product_name=product.name or '',
                    ean=ean,
                    default_code=product.default_code or '',
                    brand=brand,
                    categ_name=categ_name,
                    current_description=current_desc,
                    list_price=product.list_price or 0,
                    web_context=web_context,
                )

                # Call AI via config dispatcher (uses whichever provider is active)
                response = config.call_ai_api(prompt, max_tokens=config.max_tokens)
                elapsed = time.time() - t0

                # Parse JSON from response
                parsed = self._parse_ai_response(response)

                item.write({
                    'raw_ollama_response': response[:50000] if response else '',
                    'enriched_data': json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else '',
                    'processing_time_ollama': round(elapsed, 2),
                    'date_enriched': fields.Datetime.now(),
                    'state': 'done',
                    'error_message': False,
                })

                # Apply enrichment to product
                if parsed:
                    self._apply_enrichment(item, parsed, config)

                self.env.cr.commit()
                confidence = parsed.get('confiance', '?') if parsed else '?'
                _logger.info("AI Enrich OK: %s (confidence=%s, %.1fs)",
                             product.name, confidence, elapsed)

            except Exception as e:
                self.env.cr.rollback()
                item = item.exists()
                if item:
                    attempt = item.attempt_count + 1
                    new_state = 'skipped' if attempt >= item.max_attempts else 'error'
                    item.write({
                        'state': new_state,
                        'error_message': str(e)[:2000],
                        'attempt_count': attempt,
                    })
                    self.env.cr.commit()
                _logger.error("AI Enrich FAIL: %s: %s",
                              item.product_id.name if item.exists() else '?', str(e))

    # -------------------------------------------------------
    # JSON Parsing
    # -------------------------------------------------------
    def _parse_ai_response(self, response):
        """Extract JSON dict from the AI response text."""
        if not response:
            return None
        # Remove markdown backticks
        cleaned = re.sub(r'```(json|html)?', '', response).replace('```', '').strip()
        # Find JSON block
        json_start = cleaned.find('{')
        json_end = cleaned.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            try:
                return json.loads(cleaned[json_start:json_end])
            except json.JSONDecodeError:
                _logger.warning("Failed to parse JSON from AI response (len=%d)", len(cleaned))
        return None

    # -------------------------------------------------------
    # Apply Enrichment to Product
    # -------------------------------------------------------
    def _apply_enrichment(self, queue_item, parsed_data, config):
        """Write parsed AI data to product.template ai_* fields."""
        product = queue_item.product_id
        vals = {}

        # Map JSON keys to ai_* fields
        for json_key, field_name in AI_FIELD_MAPPING.items():
            value = parsed_data.get(json_key)
            if value and field_name in product._fields:
                if isinstance(value, list):
                    value = '\n'.join(str(v) for v in value)
                vals[field_name] = value

        # Handle list fields that need joining
        bullet_points = parsed_data.get('bullet_points')
        if bullet_points and isinstance(bullet_points, list):
            vals['ai_bullet_points'] = '\n'.join(str(bp) for bp in bullet_points)

        tags = parsed_data.get('tags')
        if tags and isinstance(tags, list):
            vals['ai_tags'] = ', '.join(str(t) for t in tags)

        selling_points = parsed_data.get('arguments_vente')
        if selling_points and isinstance(selling_points, list):
            vals['ai_selling_points'] = '\n'.join(str(sp) for sp in selling_points)

        specs = parsed_data.get('specs_techniques')
        if specs and isinstance(specs, dict):
            vals['ai_technical_specs'] = json.dumps(specs, ensure_ascii=False, indent=2)

        # Weight
        weight = parsed_data.get('poids_estime_kg')
        if weight is not None and weight != 'null':
            try:
                vals['ai_estimated_weight'] = float(weight)
            except (ValueError, TypeError):
                pass

        # Metadata
        vals['ai_enrichment_date'] = fields.Datetime.now()
        vals['ai_enrichment_source'] = f"SearXNG + {config._get_model_name()}"

        # Auto-publish to standard fields if enabled
        if config.enrichment_auto_publish:
            confidence = parsed_data.get('confiance', 'low')
            if confidence in ('high', 'medium'):
                for ai_field, std_field in STANDARD_FIELD_MAPPING.items():
                    if std_field in product._fields:
                        ai_value = vals.get(ai_field) or getattr(product, ai_field, None)
                        if ai_value:
                            current_value = getattr(product, std_field, None)
                            if config.enrichment_overwrite_existing or not current_value:
                                vals[std_field] = ai_value

        if vals:
            product.write(vals)

    # -------------------------------------------------------
    # Manual Actions
    # -------------------------------------------------------
    def action_retry(self):
        """Retry failed queue items."""
        for item in self.filtered(lambda r: r.state in ('error', 'skipped')):
            if item.raw_web_data:
                item.write({'state': 'collected', 'error_message': False})
            else:
                item.write({'state': 'pending', 'error_message': False})

    def action_reset(self):
        """Full reset to pending state."""
        self.write({
            'state': 'pending',
            'raw_web_data': False,
            'raw_ollama_response': False,
            'enriched_data': False,
            'error_message': False,
            'attempt_count': 0,
            'date_collected': False,
            'date_enriched': False,
            'processing_time_search': 0,
            'processing_time_ollama': 0,
        })

    def action_force_done(self):
        """Skip enrichment and mark as done."""
        self.filtered(lambda r: r.state != 'done').write({'state': 'done'})
