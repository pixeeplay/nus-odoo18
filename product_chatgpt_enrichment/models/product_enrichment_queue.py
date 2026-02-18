# -*- coding: utf-8 -*-
import json
import time
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests as http_requests
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

    # Computed display fields
    web_data_html = fields.Html(
        string='Résultats web (formaté)', compute='_compute_web_data_html',
        sanitize=False)
    enriched_data_html = fields.Html(
        string='Données enrichies (formaté)', compute='_compute_enriched_data_html',
        sanitize=False)
    parsed_confidence = fields.Char(
        string='Confiance', compute='_compute_parsed_fields')
    parsed_seo_title = fields.Char(
        string='Titre SEO', compute='_compute_parsed_fields')
    parsed_brand = fields.Char(
        string='Marque', compute='_compute_parsed_fields')
    parsed_category = fields.Char(
        string='Catégorie', compute='_compute_parsed_fields')
    web_result_count = fields.Integer(
        string='Nb résultats web', compute='_compute_web_data_html')

    # -------------------------------------------------------
    # Computed Display Fields
    # -------------------------------------------------------
    @api.depends('raw_web_data')
    def _compute_web_data_html(self):
        for rec in self:
            if not rec.raw_web_data:
                rec.web_data_html = False
                rec.web_result_count = 0
                continue
            try:
                results = json.loads(rec.raw_web_data)
                rec.web_result_count = len(results)
                parts = []
                for i, r in enumerate(results, 1):
                    title = r.get('title', 'Sans titre')
                    url = r.get('url', '')
                    content = (r.get('content', '') or '')[:300]
                    engine = r.get('engine', '')
                    parts.append(
                        f'<div class="card mb-2">'
                        f'<div class="card-body p-2">'
                        f'<div class="d-flex justify-content-between">'
                        f'<strong>{i}. <a href="{url}" target="_blank">{title}</a></strong>'
                        f'<span class="badge text-bg-secondary">{engine}</span>'
                        f'</div>'
                        f'<small class="text-muted">{content}</small>'
                        f'</div></div>'
                    )
                rec.web_data_html = ''.join(parts)
            except (json.JSONDecodeError, TypeError):
                rec.web_data_html = '<div class="text-muted">Erreur de parsing JSON</div>'
                rec.web_result_count = 0

    @api.depends('enriched_data')
    def _compute_enriched_data_html(self):
        LABELS = {
            'titre_seo': 'Titre SEO',
            'meta_description': 'Meta Description',
            'description_courte': 'Description courte',
            'description_longue_html': 'Description longue',
            'bullet_points': 'Points cles',
            'tags': 'Tags',
            'arguments_vente': 'Arguments de vente',
            'categorie_suggeree': 'Categorie suggeree',
            'marque_detectee': 'Marque detectee',
            'public_cible': 'Public cible',
            'specs_techniques': 'Specs techniques',
            'poids_estime_kg': 'Poids estime (kg)',
            'confiance': 'Confiance',
        }
        CONFIDENCE_BADGE = {
            'high': 'text-bg-success',
            'medium': 'text-bg-warning',
            'low': 'text-bg-danger',
        }
        for rec in self:
            if not rec.enriched_data:
                rec.enriched_data_html = False
                continue
            try:
                data = json.loads(rec.enriched_data)
                parts = ['<div class="container-fluid p-0">']
                # Confidence badge at top
                conf = data.get('confiance', '')
                if conf:
                    badge_cls = CONFIDENCE_BADGE.get(conf, 'text-bg-secondary')
                    parts.append(
                        f'<div class="mb-3"><span class="badge {badge_cls}" '
                        f'style="font-size:14px;padding:6px 12px;">'
                        f'Confiance: {conf.upper()}</span></div>'
                    )
                # Fields in a nice grid
                parts.append('<div class="row">')
                for key, label in LABELS.items():
                    if key == 'confiance':
                        continue
                    value = data.get(key)
                    if not value or value == 'null':
                        continue
                    # Format value
                    if isinstance(value, list):
                        formatted = '<ul class="mb-0 ps-3">' + ''.join(
                            f'<li>{v}</li>' for v in value) + '</ul>'
                    elif isinstance(value, dict):
                        formatted = '<table class="table table-sm table-bordered mb-0">'
                        for k, v in value.items():
                            formatted += f'<tr><td class="fw-bold">{k}</td><td>{v}</td></tr>'
                        formatted += '</table>'
                    elif key == 'description_longue_html':
                        formatted = str(value)
                    else:
                        formatted = f'<span>{value}</span>'
                    # Full width for long content
                    col_class = 'col-12' if key in (
                        'description_longue_html', 'description_courte',
                        'specs_techniques', 'bullet_points', 'arguments_vente'
                    ) else 'col-md-6'
                    parts.append(
                        f'<div class="{col_class} mb-2">'
                        f'<div style="border:1px solid #dee2e6;border-radius:4px;padding:8px;">'
                        f'<div style="color:#6c757d;font-size:12px;margin-bottom:4px;">'
                        f'<b>{label}</b></div>'
                        f'{formatted}'
                        f'</div></div>'
                    )
                parts.append('</div></div>')
                rec.enriched_data_html = ''.join(parts)
            except (json.JSONDecodeError, TypeError):
                rec.enriched_data_html = '<div style="color:#6c757d;">Erreur de parsing JSON</div>'

    @api.depends('enriched_data')
    def _compute_parsed_fields(self):
        for rec in self:
            if not rec.enriched_data:
                rec.parsed_confidence = False
                rec.parsed_seo_title = False
                rec.parsed_brand = False
                rec.parsed_category = False
                continue
            try:
                data = json.loads(rec.enriched_data)
                rec.parsed_confidence = data.get('confiance', '')
                rec.parsed_seo_title = data.get('titre_seo', '')
                rec.parsed_brand = data.get('marque_detectee', '')
                rec.parsed_category = data.get('categorie_suggeree', '')
            except (json.JSONDecodeError, TypeError):
                rec.parsed_confidence = False
                rec.parsed_seo_title = False
                rec.parsed_brand = False
                rec.parsed_category = False

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
    # STOP / RESUME Pipeline Controls
    # -------------------------------------------------------
    @api.model
    def action_pause_pipeline(self):
        """Pause the enrichment pipeline (STOP button)."""
        try:
            config = self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            raise UserError(_("No SearXNG configuration found."))
        config.sudo().write({'enrichment_paused': True})
        self.env.cr.commit()
        _logger.info("Pipeline PAUSED by user.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Pipeline en pause',
                'message': 'L\'enrichissement automatique est arrêté. Cliquez sur Reprendre pour continuer.',
                'type': 'warning',
                'sticky': False,
            },
        }

    @api.model
    def action_resume_pipeline(self):
        """Resume the enrichment pipeline (RESUME button)."""
        try:
            config = self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            raise UserError(_("No SearXNG configuration found."))
        config.sudo().write({'enrichment_paused': False})
        self.env.cr.commit()
        _logger.info("Pipeline RESUMED by user.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Pipeline relancé',
                'message': 'L\'enrichissement automatique reprend au prochain cycle.',
                'type': 'success',
                'sticky': False,
            },
        }

    # -------------------------------------------------------
    # Unified Pipeline Cron (replaces 2 separate crons)
    # -------------------------------------------------------
    @api.model
    def _cron_process_pipeline(self):
        """Unified pipeline: collect (SearXNG) first, then enrich (Ollama).

        SearXNG collection runs first so items can complete the full cycle
        (pending → collected → done) in a single pass.
        Both phases use ThreadPoolExecutor for parallel processing.
        """
        try:
            config = self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            _logger.info("Pipeline: No SearXNG config found, skipping.")
            return

        if config.enrichment_paused:
            _logger.info("Pipeline: PAUSED, skipping this cycle.")
            return

        _logger.info("Pipeline: starting cycle...")

        # Phase 1: Collect pending items via SearXNG (parallel)
        self._process_collect_parallel(config)

        # Phase 2: Enrich collected items via Ollama (parallel)
        self._process_enrich_parallel(config)

        _logger.info("Pipeline: cycle complete.")

    # -------------------------------------------------------
    # Parallel SearXNG Collection (ThreadPoolExecutor)
    # -------------------------------------------------------
    def _searxng_worker(self, searxng_params):
        """Thread worker: execute SearXNG search (pure HTTP, no ORM).

        Args:
            searxng_params: dict with base_url, engines, language, max_results,
                            timeout, delay, product_name, ean, brand, item_id
        Returns:
            dict with item_id, results, query_tech, elapsed, error
        """
        from odoo.addons.product_chatgpt_enrichment.services.searxng_client import SearXNGClient
        item_id = searxng_params['item_id']
        try:
            client = SearXNGClient(
                base_url=searxng_params['base_url'],
                engines=searxng_params['engines'],
                language=searxng_params['language'],
                max_results=searxng_params['max_results'],
                timeout=searxng_params['timeout'],
                delay_between_requests=searxng_params['delay'],
            )
            t0 = time.time()
            result = client.search_product(
                product_name=searxng_params['product_name'],
                ean=searxng_params['ean'],
                brand=searxng_params['brand'],
            )
            elapsed = time.time() - t0
            return {
                'item_id': item_id,
                'results': result.get('results', []),
                'query_tech': result.get('query_tech', ''),
                'elapsed': round(elapsed, 2),
                'error': None,
            }
        except Exception as e:
            return {
                'item_id': item_id,
                'results': [],
                'query_tech': '',
                'elapsed': 0,
                'error': str(e)[:2000],
            }

    def _process_collect_parallel(self, config):
        """Collect web data for pending items using parallel SearXNG workers."""
        batch_size = config.enrichment_batch_size_collect or 20
        items = self.search([
            ('state', '=', 'pending'),
        ], limit=batch_size, order='priority desc, date_queued asc')

        if not items:
            _logger.info("Pipeline Collect: nothing to process.")
            return

        # Mark all as collecting
        items.write({'state': 'collecting'})
        self.env.cr.commit()

        # Prepare search parameters (read ORM data in main thread)
        tasks = []
        for item in items:
            product = item.product_id
            ean = ''
            if hasattr(product, 'barcode') and product.barcode:
                ean = product.barcode
            brand = ''
            if hasattr(product, 'product_brand_id') and product.product_brand_id:
                brand = product.product_brand_id.name
            tasks.append({
                'item_id': item.id,
                'product_name': product.name or '',
                'ean': ean,
                'brand': brand,
                'base_url': config.searxng_base_url or 'http://searxng:8080',
                'engines': config.searxng_engines or 'google,duckduckgo',
                'language': config.searxng_language or 'fr-FR',
                'max_results': config.searxng_max_results or 8,
                'timeout': config.searxng_timeout or 15,
                'delay': config.searxng_delay or 3.0,
            })

        max_workers = max(1, config.searxng_parallel_workers or 4)
        _logger.info("Pipeline Collect: processing %d items with %d parallel workers...",
                      len(tasks), max_workers)

        # Run searches in parallel (pure HTTP, no ORM in threads)
        results_map = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._searxng_worker, t): t['item_id'] for t in tasks}
            for future in as_completed(futures):
                result = future.result()
                results_map[result['item_id']] = result

        # Write results back in main thread (ORM-safe)
        for item in items:
            result = results_map.get(item.id)
            if not result:
                continue
            try:
                if result['error']:
                    attempt = item.attempt_count + 1
                    new_state = 'skipped' if attempt >= item.max_attempts else 'error'
                    item.write({
                        'state': new_state,
                        'error_message': result['error'],
                        'attempt_count': attempt,
                    })
                    _logger.error("Pipeline Collect FAIL: %s: %s",
                                  item.product_id.name, result['error'][:200])
                else:
                    item.write({
                        'search_query_used': result['query_tech'],
                        'raw_web_data': json.dumps(result['results'],
                                                   ensure_ascii=False, indent=2),
                        'processing_time_search': result['elapsed'],
                        'date_collected': fields.Datetime.now(),
                        'state': 'collected',
                        'error_message': False,
                    })
                    _logger.info("Pipeline Collect OK: %s (%d results, %.1fs)",
                                 item.product_id.name, len(result['results']),
                                 result['elapsed'])
            except Exception as e:
                _logger.error("Pipeline Collect write error for item %s: %s", item.id, e)
        self.env.cr.commit()

    # -------------------------------------------------------
    # Parallel Ollama Enrichment (ThreadPoolExecutor)
    # -------------------------------------------------------
    def _ollama_worker(self, ollama_params):
        """Thread worker: call Ollama API (pure HTTP, no ORM).

        Args:
            ollama_params: dict with base_url, model, prompt, max_tokens,
                           temperature, item_id
        Returns:
            dict with item_id, response, elapsed, error
        """
        item_id = ollama_params['item_id']
        try:
            url = f"{ollama_params['base_url']}/api/chat"
            data = {
                'model': ollama_params['model'],
                'messages': [
                    {'role': 'system', 'content': 'Expert product marketer. HTML output.'},
                    {'role': 'user', 'content': ollama_params['prompt']},
                ],
                'stream': False,
                'keep_alive': ollama_params.get('keep_alive', '10m'),
                'options': {
                    'num_predict': ollama_params['max_tokens'],
                    'temperature': ollama_params['temperature'],
                    'num_ctx': ollama_params.get('num_ctx', 4096),
                    'num_gpu': ollama_params.get('num_gpu', 99),
                },
            }
            timeout = ollama_params.get('timeout', 180)
            t0 = time.time()
            resp = http_requests.post(url, json=data, timeout=timeout)
            elapsed = time.time() - t0

            if resp.status_code != 200:
                return {
                    'item_id': item_id,
                    'response': '',
                    'elapsed': round(elapsed, 2),
                    'error': f"Ollama HTTP {resp.status_code}: {resp.text[:500]}",
                }

            res = resp.json()
            if 'error' in res:
                return {
                    'item_id': item_id,
                    'response': '',
                    'elapsed': round(elapsed, 2),
                    'error': f"Ollama error: {res.get('error', 'Unknown')}",
                }

            msg = res.get('message', {})
            content = msg.get('content', '') if isinstance(msg, dict) else ''
            if not content:
                content = res.get('response', '')
            # Clean markdown
            content = content.replace('```html', '').replace('```json', '').replace('```', '').strip()

            return {
                'item_id': item_id,
                'response': content,
                'elapsed': round(elapsed, 2),
                'error': None,
            }
        except Exception as e:
            return {
                'item_id': item_id,
                'response': '',
                'elapsed': 0,
                'error': str(e)[:2000],
            }

    def _process_enrich_parallel(self, config):
        """Enrich collected items using parallel Ollama workers."""
        # Safety check: detect and fix wrong model for Ollama
        resolved_model = config._get_model_name()
        model_override = None

        OPENAI_MODELS = {'gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo'}
        if config.provider == 'ollama' and resolved_model in OPENAI_MODELS:
            _logger.warning("Pipeline Enrich: model '%s' invalid for Ollama, auto-detecting...",
                            resolved_model)
            model_override = self._auto_detect_ollama_model(config)
            if model_override:
                config.sudo().write({'ai_model_name': model_override, 'model_id': False})
                self.env.cr.commit()
                _logger.info("Pipeline Enrich: fixed config → model='%s'", model_override)
            else:
                _logger.error("Pipeline Enrich: no Ollama models found! Aborting.")
                return

        effective_model = model_override or resolved_model
        batch_size = config.enrichment_batch_size_enrich or 10
        items = self.search([
            ('state', '=', 'collected'),
        ], limit=batch_size, order='priority desc, date_collected asc')

        if not items:
            _logger.info("Pipeline Enrich: nothing to process.")
            return

        # Mark all as enriching
        items.write({'state': 'enriching'})
        self.env.cr.commit()

        # Build prompts in main thread (needs ORM)
        prompt_template = config.enrichment_prompt_template or ''
        base_url = config._get_base_url()
        tasks = []
        for item in items:
            product = item.product_id
            # Build web context
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
            tasks.append({
                'item_id': item.id,
                'prompt': prompt,
                'base_url': base_url,
                'model': effective_model,
                'max_tokens': config.max_tokens or 4000,
                'temperature': config.temperature if config.temperature is not None else 0.3,
                'timeout': config.ollama_request_timeout or 180,
                'num_ctx': config.ollama_num_ctx or 4096,
                'num_gpu': config.ollama_num_gpu if config.ollama_num_gpu is not None else 99,
                'keep_alive': config.ollama_keep_alive or '10m',
            })

        max_workers = max(1, config.ollama_parallel_workers or 2)
        _logger.info("Pipeline Enrich: processing %d items with %d parallel workers (model=%s)...",
                      len(tasks), max_workers, effective_model)

        # Run AI calls in parallel (pure HTTP, no ORM in threads)
        results_map = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._ollama_worker, t): t['item_id'] for t in tasks}
            for future in as_completed(futures):
                result = future.result()
                results_map[result['item_id']] = result

        # Write results back in main thread (ORM-safe)
        for item in items:
            result = results_map.get(item.id)
            if not result:
                continue
            try:
                if result['error']:
                    attempt = item.attempt_count + 1
                    new_state = 'skipped' if attempt >= item.max_attempts else 'error'
                    item.write({
                        'state': new_state,
                        'error_message': result['error'],
                        'attempt_count': attempt,
                    })
                    _logger.error("Pipeline Enrich FAIL: %s: %s",
                                  item.product_id.name, result['error'][:200])
                else:
                    parsed = self._parse_ai_response(result['response'])
                    item.write({
                        'raw_ollama_response': result['response'][:50000],
                        'enriched_data': json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else '',
                        'processing_time_ollama': result['elapsed'],
                        'date_enriched': fields.Datetime.now(),
                        'state': 'done',
                        'error_message': False,
                    })
                    if parsed:
                        self._apply_enrichment(item, parsed, config)
                    confidence = parsed.get('confiance', '?') if parsed else '?'
                    _logger.info("Pipeline Enrich OK: %s (confidence=%s, %.1fs)",
                                 item.product_id.name, confidence, result['elapsed'])
            except Exception as e:
                _logger.error("Pipeline Enrich write error for item %s: %s", item.id, e)
        self.env.cr.commit()

    # -------------------------------------------------------
    # Legacy Cron 1: SearXNG Web Data Collection (kept as fallback)
    # -------------------------------------------------------
    @api.model
    def _cron_collect_web_data(self):
        """Process pending queue items via SearXNG (sequential fallback)."""
        try:
            config = self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            _logger.info("AI Queue Collect: No SearXNG config found, skipping.")
            return

        if config.enrichment_paused:
            _logger.info("AI Queue Collect: pipeline paused, skipping.")
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
    # Legacy Cron 2: Ollama AI Enrichment (kept as fallback)
    # -------------------------------------------------------
    @api.model
    def _cron_enrich_ollama(self):
        """Process collected queue items via Ollama (sequential fallback)."""
        try:
            config = self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            _logger.info("AI Queue Enrich: No SearXNG config found, skipping.")
            return

        if config.enrichment_paused:
            _logger.info("AI Queue Enrich: pipeline paused, skipping.")
            return

        # -------------------------------------------------------
        # Safety check: detect and fix wrong model for Ollama
        # -------------------------------------------------------
        resolved_model = config._get_model_name()
        model_override = None

        _logger.info(
            "AI Queue Enrich CONFIG: id=%s name='%s' provider=%s "
            "ai_model_name='%s' model_id=%s base_url='%s' "
            "resolved_model='%s'",
            config.id, config.name, config.provider,
            config.ai_model_name,
            config.model_id.id if config.model_id else False,
            config.base_url, resolved_model,
        )

        # If provider is Ollama but model looks like OpenAI, auto-detect
        OPENAI_MODELS = {'gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo'}
        if config.provider == 'ollama' and resolved_model in OPENAI_MODELS:
            _logger.warning(
                "AI Queue Enrich: model '%s' is invalid for Ollama! "
                "Auto-detecting available models...", resolved_model
            )
            model_override = self._auto_detect_ollama_model(config)
            if model_override:
                _logger.info("AI Queue Enrich: auto-detected model '%s'", model_override)
                # Fix the config in DB for future runs
                config.sudo().write({
                    'ai_model_name': model_override,
                    'model_id': False,
                })
                self.env.cr.commit()
                _logger.info("AI Queue Enrich: fixed config DB → ai_model_name='%s'", model_override)
            else:
                _logger.error("AI Queue Enrich: no Ollama models found! Aborting.")
                return

        batch_size = config.enrichment_batch_size_enrich or 10
        items = self.search([
            ('state', '=', 'collected'),
        ], limit=batch_size, order='priority desc, date_collected asc')

        if not items:
            _logger.info("AI Queue Enrich: nothing to process.")
            return

        _logger.info("AI Queue Enrich: processing %d items (model=%s)...",
                      len(items), model_override or resolved_model)
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

                # Call AI via config dispatcher
                response = config.call_ai_api(
                    prompt, max_tokens=config.max_tokens,
                    model_override=model_override,
                )
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
    # Ollama Model Auto-Detection
    # -------------------------------------------------------
    def _auto_detect_ollama_model(self, config):
        """Query Ollama /api/tags to find available models. Returns model name or None."""
        base_url = config._get_base_url()
        url = f"{base_url}/api/tags"
        _logger.info("Auto-detecting Ollama models at %s", url)
        try:
            resp = http_requests.get(url, timeout=10)
            if resp.status_code != 200:
                _logger.error("Ollama /api/tags failed: %s %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            models = data.get('models', [])
            available = [m.get('name', '') for m in models if m.get('name')]
            _logger.info("Ollama models available: %s", available)

            if not available:
                return None

            # Priority: llama3.1:8b > llama3.1 > mistral > first available
            for name in available:
                if name == 'llama3.1:8b' or name == 'llama3.1':
                    return name
            for name in available:
                if 'llama3.1' in name.lower():
                    return name
            for name in available:
                if name == 'mistral' or name.startswith('mistral:'):
                    return name
            # Fallback: first available model
            return available[0]
        except Exception as e:
            _logger.error("Ollama /api/tags error: %s", str(e))
            return None

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

    @api.model
    def action_process_queue_now(self):
        """Manual trigger: run unified pipeline immediately (bypasses pause)."""
        _logger.info("Manual queue processing triggered (bypasses pause)...")
        try:
            config = self.env['chatgpt.config'].get_searxng_config()
        except Exception:
            raise UserError(_("No SearXNG configuration found."))
        # Phase 1: Collect pending items via SearXNG (parallel)
        self._process_collect_parallel(config)
        # Phase 2: Enrich collected items via Ollama (parallel)
        self._process_enrich_parallel(config)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Traitement lancé',
                'message': 'Pipeline parallèle exécuté (collecte + enrichissement).',
                'type': 'success',
                'sticky': False,
            },
        }
