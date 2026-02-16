# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

# Default base URLs per provider
PROVIDER_DEFAULTS = {
    'openai': {'base_url': 'https://api.openai.com', 'endpoint': '/v1/chat/completions', 'model': 'gpt-4o-mini'},
    'gemini': {'base_url': 'https://generativelanguage.googleapis.com', 'endpoint': '', 'model': 'gemini-2.0-flash'},
    'anthropic': {'base_url': 'https://api.anthropic.com', 'endpoint': '/v1/messages', 'model': 'claude-sonnet-4-5-20250929'},
    'perplexity': {'base_url': 'https://api.perplexity.ai', 'endpoint': '/chat/completions', 'model': 'llama-3.1-sonar-small-128k-online'},
    'ollama': {'base_url': 'http://localhost:11434', 'endpoint': '/api/chat', 'model': 'llama3.2'},
    'llamacpp': {'base_url': 'http://localhost:8080', 'endpoint': '/v1/chat/completions', 'model': 'default'},
}


class ChatGPTConfig(models.Model):
    _name = 'chatgpt.config'
    _description = 'AI Configuration'
    _rec_name = 'name'

    name = fields.Char(string='Config Name', default='Main AI Configuration')

    provider = fields.Selection([
        ('openai', 'OpenAI'),
        ('gemini', 'Google Gemini'),
        ('anthropic', 'Anthropic Claude'),
        ('perplexity', 'Perplexity (Web Search)'),
        ('ollama', 'Ollama (Local / Remote)'),
        ('llamacpp', 'Llama.cpp (Local / Remote)'),
    ], string='AI Provider', default='openai', required=True,
       help="Choose your AI provider. Cloud (OpenAI, Gemini...) or Local (Ollama, Llama.cpp).")

    api_key = fields.Char(
        string='API Key / Token',
        help="API key for cloud providers. For Ollama local, leave empty. "
             "For Ollama web search, get a key at https://ollama.com/settings/keys"
    )

    base_url = fields.Char(
        string='Base URL',
        help="Server address. Examples:\n"
             "- Ollama local: http://localhost:11434\n"
             "- Ollama on Mac Mini: http://192.168.x.x:11434\n"
             "- Llama.cpp: http://localhost:8080\n"
             "- OpenAI: https://api.openai.com"
    )

    api_endpoint = fields.Char(
        string='API Endpoint Path',
        help="Path appended to Base URL. Auto-set per provider.\n"
             "- Ollama: /api/chat\n"
             "- Llama.cpp: /v1/chat/completions\n"
             "- OpenAI: /v1/chat/completions"
    )

    ollama_api_mode = fields.Selection([
        ('native', 'Ollama Native (/api/chat)'),
        ('openai', 'OpenAI Compatible (/v1/chat/completions)'),
    ], string='Ollama API Mode', default='native',
       help="Native: uses Ollama's /api/chat endpoint (recommended).\n"
            "OpenAI Compatible: uses /v1/chat/completions (useful for tools expecting OpenAI API).")

    ollama_web_search = fields.Boolean(
        string='Ollama Web Search',
        default=False,
        help="Enable Ollama's built-in web search. Requires an API key from https://ollama.com/settings/keys. "
             "Uses ollama.com/api/web_search to search the web before answering."
    )

    model_id = fields.Many2one('chatgpt.model', string='AI Model',
                               help='Select a discovered model or enter one manually')

    ai_model_name = fields.Char(
        string='Model Name (Manual)',
        help='Manual model name if discovery is not used. Examples:\n'
             '- Ollama: llama3.2, qwen3:4b, mistral\n'
             '- Llama.cpp: default (or the loaded model name)\n'
             '- OpenAI: gpt-4o-mini, gpt-4o\n'
             '- Anthropic: claude-sonnet-4-5-20250929'
    )

    auto_enrich = fields.Boolean(string='Auto-Enrich New Products', default=False)

    use_web_search = fields.Boolean(
        string='Use Web Search',
        default=False,
        help="Perform a web search before AI enrichment for additional context.")

    use_deep_enrichment = fields.Boolean(
        string='Deep Enrichment (SerpApi + ScrapingBee)',
        default=False,
        help='Advanced: Odoo searches Google and scrapes top websites for real-time context.')

    serpapi_key = fields.Char(string='SerpApi Key', help='For product and image search')
    scrapingbee_key = fields.Char(string='ScrapingBee Key', help='For anti-bot bypass and data extraction')

    media_discovery = fields.Boolean(
        string='Discover Media', default=False,
        help='Auto-discover and download images/videos from AI responses.')

    max_tokens = fields.Integer(string='Max Tokens', default=2000,
        help="Max response length. Higher = longer but costlier.")
    temperature = fields.Float(string='Temperature', default=0.7,
        help="Creativity (0.0 = strict, 1.0 = creative).")

    prompt_ids = fields.One2many('chatgpt.product.prompt', 'config_id', string='Prompts')
    active = fields.Boolean(default=True)

    price_alignment_strategy = fields.Selection([
        ('none', 'No Alignment'),
        ('lowest', 'Match Lowest Competitor'),
        ('average', 'Match Average Competitor')
    ], string='Price Alignment Strategy', default='none',
       help="Auto-suggest price based on competitors.")

    price_alignment_offset = fields.Float(
        string='Price Offset (Fixed)', default=0.0,
        help="Fixed amount to add/subtract (e.g., -0.01 = 1 cent cheaper).")

    price_alignment_offset_type = fields.Selection([
        ('fixed', 'Fixed Amount'),
        ('percentage', 'Percentage')
    ], string='Offset Type', default='fixed')

    price_alignment_offset_pct = fields.Float(
        string='Price Offset (%)', default=0.0,
        help="Percentage to add/subtract (e.g., -5.0 = 5% cheaper).")

    target_competitors = fields.Text(
        string='Target Competitors',
        placeholder="amazon.fr\nfnac.com\ndarty.com",
        help="Domains to prioritize during search (one per line).")

    prompt_technical_research = fields.Text(
        string='Technical Research Prompt',
        default="""Analyze the product '{product_name}'.
Search the web to find its exact technical specifications.
Focus on: Dimensions (length, width, height), Weight (in kg), Material, and key technical features.
Return the result as a detailed technical summary in {language}.""",
        help="Prompt for the 'Web Search' phase.")

    prompt_deep_enrichment = fields.Text(
        string='Deep Enrichment Prompt',
        default="""System: You are a professional market analyst.
Goal: Extract structured data for '{product_name}' using the PROVIDED CONTEXT (scraped from web).
Required Output (JSON ONLY):
{{
  "weight": float (kg),
  "volume": float (m3),
  "description_sale": "Short 1-sentence sales pitch",
  "prices_france": [{{ "source": "domain.com", "price": float, "url": "url" }}],
  "youtube_videos": [{{ "name": "Title", "url": "url" }}],
  "technical_bullets": ["Spec 1", "Spec 2"]
}}
Answer in {language}.""",
        help="System prompt for deep enrichment.")

    # Advanced Search Settings
    serpapi_hl = fields.Char(string='Google Host Language', default='fr')
    serpapi_gl = fields.Char(string='Google Geolocation', default='fr')
    max_scrape_pages = fields.Integer(string='Max Pages to Scrape', default=3)
    debug_show_raw_results = fields.Boolean(string='Show Raw Search Data', default=False)

    auto_enrich_enabled = fields.Boolean(string='Automated Enrichment', default=False)
    auto_enrich_interval = fields.Selection([
        ('2', 'Every 2 Hours'),
        ('4', 'Every 4 Hours'),
        ('24', 'Every 24 Hours'),
    ], string='Frequency', default='24')

    model_discovery_results = fields.Text(string='Discovered Models', readonly=True)
    last_test_result = fields.Text(string='Last Test Result', readonly=True)

    # -------------------------------------------------------
    # Computed helpers
    # -------------------------------------------------------
    @api.onchange('provider')
    def _onchange_provider(self):
        """Set sensible defaults when provider changes."""
        defaults = PROVIDER_DEFAULTS.get(self.provider, {})
        if defaults:
            if not self.base_url:
                self.base_url = defaults.get('base_url', '')
            self.api_endpoint = defaults.get('endpoint', '')
            if not self.ai_model_name and not self.model_id:
                self.ai_model_name = defaults.get('model', '')

    @api.onchange('ollama_api_mode')
    def _onchange_ollama_api_mode(self):
        if self.provider == 'ollama':
            if self.ollama_api_mode == 'native':
                self.api_endpoint = '/api/chat'
            else:
                self.api_endpoint = '/v1/chat/completions'

    @api.model
    def get_active_config(self):
        config = self.search([('active', '=', True)], limit=1)
        if not config:
            raise UserError(_('No active AI configuration found. Go to AI Enrichment > Settings > AI Providers.'))
        return config

    def _get_base_url(self):
        """Get the base URL with sensible defaults per provider."""
        if self.base_url:
            return self.base_url.rstrip('/')
        return PROVIDER_DEFAULTS.get(self.provider, {}).get('base_url', 'http://localhost:11434')

    def _get_full_url(self, endpoint_override=None):
        """Build the full API URL from base_url + endpoint."""
        base = self._get_base_url()
        endpoint = endpoint_override or self.api_endpoint or ''
        if endpoint and not endpoint.startswith('/'):
            endpoint = '/' + endpoint
        return base + endpoint

    def _get_model_name(self):
        """Get model name from Many2one or manual field."""
        if self.model_id:
            return self.model_id.code
        if self.ai_model_name:
            return self.ai_model_name
        return PROVIDER_DEFAULTS.get(self.provider, {}).get('model', 'gpt-4o-mini')

    def _get_headers(self):
        """Build auth headers based on provider."""
        if self.provider == 'anthropic':
            return {
                'x-api-key': self.api_key or '',
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            }
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    # -------------------------------------------------------
    # Test Connection
    # -------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()
        try:
            # First test: can we reach the server?
            base = self._get_base_url()
            _logger.info("Testing connection to %s (%s)...", self.provider, base)

            if self.provider == 'ollama':
                # Test Ollama server is up
                test_url = f"{base}/api/tags"
                resp = requests.get(test_url, timeout=10)
                if resp.status_code != 200:
                    raise UserError(_(
                        "Cannot reach Ollama at %s\n"
                        "Status: %s\n"
                        "Response: %s\n\n"
                        "Make sure Ollama is running:\n"
                        "  ollama serve\n"
                        "Or check the IP/port if it's on another machine."
                    ) % (test_url, resp.status_code, resp.text[:300]))

                models = resp.json().get('models', [])
                model_names = [m.get('name', '?') for m in models]
                _logger.info("Ollama OK - %d models: %s", len(models), model_names)

            # Second test: actual AI call
            res = self.call_ai_api("Say 'Hello from Odoo!' in one sentence.", max_tokens=50)
            self.last_test_result = f"OK: {res[:200]}" if res else "OK (empty response)"

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection OK!'),
                    'message': _('Provider: %s\nModel: %s\nResponse: %s') % (
                        self.provider, self._get_model_name(), (res or '')[:100],
                    ),
                    'type': 'success',
                    'sticky': True,
                }
            }
        except UserError:
            raise
        except requests.exceptions.ConnectionError as e:
            raise UserError(_(
                "Cannot connect to %s at %s\n\n"
                "Error: %s\n\n"
                "Possible fixes:\n"
                "- Check that the server is running\n"
                "- Verify the IP address and port\n"
                "- If remote, check firewall rules"
            ) % (self.provider, self._get_base_url(), str(e)[:300]))
        except Exception as e:
            raise UserError(_('Connection failed: %s') % str(e))

    # -------------------------------------------------------
    # Model Discovery
    # -------------------------------------------------------
    def action_discover_models(self):
        self.ensure_one()
        models_data = []

        try:
            if self.provider == 'ollama':
                url = f"{self._get_base_url()}/api/tags"
                _logger.info("Discovering Ollama models at %s", url)
                response = requests.get(url, timeout=10)
                if response.status_code != 200:
                    raise UserError(_(
                        "Ollama discovery failed.\nURL: %s\nStatus: %s\nResponse: %s"
                    ) % (url, response.status_code, response.text[:300]))
                data = response.json()
                for m in data.get('models', []):
                    name = m.get('name', '')
                    size = m.get('size', 0)
                    size_gb = round(size / (1024**3), 1) if size else 0
                    label = f"{name} ({size_gb}GB)" if size_gb else name
                    models_data.append({'name': label, 'code': name})

            elif self.provider == 'llamacpp':
                url = f"{self._get_base_url()}/v1/models"
                _logger.info("Discovering Llama.cpp models at %s", url)
                headers = self._get_headers()
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    for m in response.json().get('data', []):
                        models_data.append({'name': m.get('id', '?'), 'code': m.get('id', '')})
                else:
                    # Llama.cpp may not support /v1/models - add default
                    models_data.append({'name': 'default', 'code': 'default'})

            elif self.provider == 'openai':
                url = "https://api.openai.com/v1/models"
                headers = self._get_headers()
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    for m in response.json().get('data', []):
                        mid = m.get('id', '')
                        # Filter to chat models only
                        if any(k in mid for k in ['gpt', 'o1', 'o3', 'chatgpt']):
                            models_data.append({'name': mid, 'code': mid})

            elif self.provider == 'perplexity':
                url = "https://api.perplexity.ai/v1/models"
                headers = self._get_headers()
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    for m in response.json().get('data', []):
                        models_data.append({'name': m.get('id', '?'), 'code': m.get('id', '')})

            elif self.provider == 'anthropic':
                url = "https://api.anthropic.com/v1/models"
                headers = self._get_headers()
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    for m in response.json().get('data', []):
                        models_data.append({
                            'name': m.get('display_name', m.get('id', '?')),
                            'code': m.get('id', ''),
                        })

            elif self.provider == 'gemini':
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    for m in response.json().get('models', []):
                        models_data.append({
                            'name': m.get('displayName', '?'),
                            'code': m.get('name', '').split('/')[-1],
                        })

            if models_data:
                Model = self.env['chatgpt.model']
                created = 0
                for m in models_data:
                    if not m.get('code'):
                        continue
                    existing = Model.search([
                        ('code', '=', m['code']),
                        ('provider', '=', self.provider),
                    ], limit=1)
                    if not existing:
                        Model.create({
                            'name': m['name'],
                            'code': m['code'],
                            'provider': self.provider,
                        })
                        created += 1

                self.model_discovery_results = (
                    "Found %d models (%d new).\n%s" % (
                        len(models_data), created,
                        '\n'.join(m['code'] for m in models_data[:20]),
                    )
                )
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Models Discovered'),
                        'message': _('%d models found (%d new)') % (len(models_data), created),
                        'type': 'success',
                    }
                }

        except UserError:
            raise
        except requests.exceptions.ConnectionError as e:
            raise UserError(_(
                "Cannot connect to %s at %s\n\nError: %s\n\n"
                "Make sure the server is running and accessible."
            ) % (self.provider, self._get_base_url(), str(e)[:300]))
        except Exception as e:
            raise UserError(_("Discovery failed: %s") % str(e))

        raise UserError(_("No models found. Check your connection settings."))

    # -------------------------------------------------------
    # Ollama Web Search
    # -------------------------------------------------------
    def _ollama_web_search(self, query, max_results=5):
        """Use Ollama's web search API (requires ollama.com API key)."""
        if not self.api_key:
            _logger.warning("Ollama web search requires an API key from ollama.com/settings/keys")
            return []
        url = "https://ollama.com/api/web_search"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        data = {'query': query, 'max_results': min(max_results, 10)}
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=15)
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    return results
                return results.get('results', [])
            _logger.warning("Ollama web search failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            _logger.error("Ollama web search error: %s", e)
        return []

    # -------------------------------------------------------
    # SerpApi / ScrapingBee (unchanged)
    # -------------------------------------------------------
    def _search_with_serpapi(self, query, engine='google'):
        if not self.serpapi_key:
            return []
        url = "https://serpapi.com/search"
        params = {
            "q": query, "engine": engine,
            "api_key": self.serpapi_key,
            "hl": self.serpapi_hl or 'fr',
            "gl": self.serpapi_gl or 'fr',
            "num": 10,
        }
        try:
            res = requests.get(url, params=params, timeout=10).json()
            if engine == 'google_images':
                return res.get('images_results', [])
            return res.get('organic_results', [])
        except Exception as e:
            _logger.error("SerpApi error: %s", str(e))
            return []

    def _scrape_with_scrapingbee(self, url):
        if not self.scrapingbee_key:
            return ""
        sb_url = "https://app.scrapingbee.com/api/v1"
        params = {
            "api_key": self.scrapingbee_key,
            "url": url,
            "render_js": "false",
            "extract_rules": '{"content": "body"}',
        }
        try:
            res = requests.get(sb_url, params=params, timeout=30).json()
            return res.get('content', '')[:10000]
        except Exception as e:
            _logger.error("ScrapingBee error: %s", str(e))
            return ""

    # -------------------------------------------------------
    # Cron
    # -------------------------------------------------------
    @api.model
    def action_cron_automated_alignment(self):
        config = self.get_active_config()
        if not config or not config.auto_enrich_enabled:
            return
        _logger.info("Starting automated market alignment...")
        products = self.env['product.template'].search([
            ('active', '=', True),
            ('chatgpt_auto_align', '=', True),
        ])
        for product in products:
            try:
                product._enrich_with_chatgpt()
                self.env.cr.commit()
            except Exception as e:
                _logger.error("Auto-align error for %s: %s", product.name, str(e))
        _logger.info("Automated alignment done for %s products.", len(products))

    def write(self, vals):
        res = super().write(vals)
        if 'auto_enrich_interval' in vals or 'auto_enrich_enabled' in vals:
            self._update_cron_interval()
        return res

    def _update_cron_interval(self):
        cron = self.env.ref('product_chatgpt_enrichment.ir_cron_auto_align_market', raise_if_not_found=False)
        if cron:
            interval = int(self.auto_enrich_interval or 24)
            cron.write({
                'interval_number': interval,
                'interval_type': 'hours',
                'active': self.auto_enrich_enabled,
            })

    # -------------------------------------------------------
    # Main AI dispatcher
    # -------------------------------------------------------
    def call_ai_api(self, prompt, max_tokens=None, temperature=None):
        self.ensure_one()
        _logger.info("AI call [%s] model=%s url=%s",
                      self.provider, self._get_model_name(), self._get_base_url())

        # If Ollama web search is enabled, prepend web context
        if self.provider == 'ollama' and self.ollama_web_search:
            # Extract a search query from the prompt (first 100 chars)
            search_query = prompt[:150].replace('\n', ' ')
            web_results = self._ollama_web_search(search_query)
            if web_results:
                context_parts = []
                for r in web_results[:5]:
                    title = r.get('title', '')
                    content = r.get('content', r.get('snippet', ''))[:500]
                    url = r.get('url', '')
                    context_parts.append(f"[{title}]({url}): {content}")
                web_context = "\n\n".join(context_parts)
                prompt = (
                    f"Use the following web search results as context:\n\n"
                    f"{web_context}\n\n"
                    f"Now answer the original question:\n{prompt}"
                )

        if self.provider == 'openai':
            return self._call_openai_compatible(prompt, max_tokens, temperature)
        elif self.provider == 'gemini':
            return self._call_gemini(prompt, max_tokens, temperature)
        elif self.provider == 'anthropic':
            return self._call_anthropic(prompt, max_tokens, temperature)
        elif self.provider == 'perplexity':
            return self._call_openai_compatible(prompt, max_tokens, temperature,
                                                 base_url='https://api.perplexity.ai',
                                                 endpoint='/chat/completions')
        elif self.provider == 'ollama':
            return self._call_ollama(prompt, max_tokens, temperature)
        elif self.provider == 'llamacpp':
            return self._call_openai_compatible(prompt, max_tokens, temperature)
        return False

    # -------------------------------------------------------
    # OpenAI-compatible endpoint (works for OpenAI, Llama.cpp, Ollama /v1)
    # -------------------------------------------------------
    def _call_openai_compatible(self, prompt, max_tokens, temperature,
                                 base_url=None, endpoint=None):
        """Call any OpenAI-compatible /v1/chat/completions endpoint."""
        if base_url:
            url = base_url.rstrip('/') + (endpoint or '/v1/chat/completions')
        else:
            url = self._get_full_url(endpoint)

        headers = self._get_headers()
        data = {
            'model': self._get_model_name(),
            'messages': [
                {'role': 'system', 'content': 'Expert product marketer. HTML output.'},
                {'role': 'user', 'content': prompt},
            ],
            'max_tokens': max_tokens or self.max_tokens,
            'temperature': temperature if temperature is not None else self.temperature,
        }
        _logger.info("OpenAI-compatible POST %s (model=%s)", url, data['model'])
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=120)
            if resp.status_code != 200:
                raise UserError(_(
                    "%s API error.\nURL: %s\nStatus: %s\nResponse: %s"
                ) % (self.provider, url, resp.status_code, resp.text[:500]))
            res = resp.json()
            if 'error' in res:
                raise UserError(_("%s Error: %s") % (
                    self.provider, res['error'].get('message', res['error'])))
            return self._format_response(res['choices'][0]['message']['content'])
        except UserError:
            raise
        except requests.exceptions.ConnectionError:
            raise UserError(_(
                "Cannot connect to %s\nURL: %s\n\n"
                "Check that the server is running and accessible."
            ) % (self.provider, url))
        except (KeyError, IndexError) as e:
            raise UserError(_(
                "Invalid response from %s.\nURL: %s\nError: %s"
            ) % (self.provider, url, str(e)))

    # -------------------------------------------------------
    # Ollama native /api/chat endpoint
    # -------------------------------------------------------
    def _call_ollama(self, prompt, max_tokens, temperature):
        """Call Ollama using native /api/chat or OpenAI-compatible mode."""
        if self.ollama_api_mode == 'openai':
            return self._call_openai_compatible(prompt, max_tokens, temperature)

        # Native Ollama /api/chat
        base = self._get_base_url()
        url = f"{base}/api/chat"
        model = self._get_model_name()

        data = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': 'Expert product marketer. HTML output.'},
                {'role': 'user', 'content': prompt},
            ],
            'stream': False,
            'options': {
                'num_predict': max_tokens or self.max_tokens,
                'temperature': temperature if temperature is not None else self.temperature,
            },
        }
        _logger.info("Ollama native POST %s (model=%s)", url, model)
        try:
            resp = requests.post(url, json=data, timeout=180)
            if resp.status_code != 200:
                raise UserError(_(
                    "Ollama error.\nURL: %s\nStatus: %s\nResponse: %s\n\n"
                    "Tips:\n"
                    "- Is Ollama running? (ollama serve)\n"
                    "- Is the model downloaded? (ollama pull %s)\n"
                    "- Check IP/port if remote"
                ) % (url, resp.status_code, resp.text[:500], model))
            res = resp.json()
            if 'error' in res:
                raise UserError(_("Ollama Error: %s") % res.get('error', 'Unknown'))
            # /api/chat returns: {"message": {"role": "assistant", "content": "..."}}
            msg = res.get('message', {})
            content = msg.get('content', '') if isinstance(msg, dict) else ''
            if not content:
                # Fallback: /api/generate style response
                content = res.get('response', '')
            return self._format_response(content)
        except UserError:
            raise
        except requests.exceptions.ConnectionError:
            raise UserError(_(
                "Cannot connect to Ollama at %s\n\n"
                "Make sure Ollama is running:\n"
                "  ollama serve\n\n"
                "If it's on another machine (e.g. Mac Mini), use its IP:\n"
                "  http://192.168.x.x:11434"
            ) % base)
        except Exception as e:
            raise UserError(_("Ollama call failed: %s") % str(e))

    # -------------------------------------------------------
    # Gemini
    # -------------------------------------------------------
    def _call_gemini(self, prompt, max_tokens, temperature):
        model = self._get_model_name()
        if '/' not in model:
            model = f"models/{model}"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={self.api_key}"
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens or self.max_tokens,
                "temperature": temperature if temperature is not None else self.temperature,
            }
        }
        try:
            resp = requests.post(url, json=data, timeout=60)
            if resp.status_code != 200:
                raise UserError(_(
                    "Gemini error.\nStatus: %s\nResponse: %s"
                ) % (resp.status_code, resp.text[:500]))
            res = resp.json()
            if 'error' in res:
                raise UserError(_("Gemini Error: %s") % res['error'].get('message', 'Unknown'))
            return self._format_response(res['candidates'][0]['content']['parts'][0]['text'])
        except UserError:
            raise
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from Gemini. Check API Key and Model."))

    # -------------------------------------------------------
    # Anthropic
    # -------------------------------------------------------
    def _call_anthropic(self, prompt, max_tokens, temperature):
        headers = self._get_headers()
        url = 'https://api.anthropic.com/v1/messages'
        data = {
            'model': self._get_model_name(),
            'max_tokens': max_tokens or self.max_tokens,
            'temperature': temperature if temperature is not None else self.temperature,
            'messages': [{'role': 'user', 'content': prompt}],
        }
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            if resp.status_code != 200:
                raise UserError(_(
                    "Anthropic error.\nStatus: %s\nResponse: %s"
                ) % (resp.status_code, resp.text[:500]))
            res = resp.json()
            if 'error' in res:
                raise UserError(_("Anthropic Error: %s") % res['error'].get('message', 'Unknown'))
            return self._format_response(res['content'][0]['text'])
        except UserError:
            raise
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from Anthropic. Check API Key and Model."))

    # -------------------------------------------------------
    # Format
    # -------------------------------------------------------
    def _format_response(self, content):
        if not content:
            return ""
        content = content.replace('```html', '').replace('```', '').strip()
        return content


class ChatGPTProductPrompt(models.Model):
    _name = 'chatgpt.product.prompt'
    _description = 'AI Product Prompt'
    _order = 'sequence, id'

    name = fields.Char(string='Description', required=True)
    sequence = fields.Integer(default=10)
    config_id = fields.Many2one('chatgpt.config', string='Configuration', ondelete='cascade')

    prompt_template = fields.Text(
        string='Prompt Template', required=True,
        help="Use {product_name} as placeholder.")

    target_field_id = fields.Many2one(
        'ir.model.fields', string='Target Field',
        domain="[('model', '=', 'product.template'), ('ttype', 'in', ['char', 'text', 'html'])]")

    language = fields.Selection([
        ('fr_FR', 'French'),
        ('en_US', 'English'),
        ('de_DE', 'German'),
        ('es_ES', 'Spanish'),
        ('it_IT', 'Italian'),
    ], string='Language', default='fr_FR')

    active = fields.Boolean(default=True)


class ChatGPTModel(models.Model):
    _name = 'chatgpt.model'
    _description = 'AI Model Discovery'
    _order = 'provider, name'

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    provider = fields.Selection([
        ('openai', 'OpenAI'),
        ('gemini', 'Google Gemini'),
        ('anthropic', 'Anthropic Claude'),
        ('perplexity', 'Perplexity (Web Search)'),
        ('ollama', 'Ollama (Local / Remote)'),
        ('llamacpp', 'Llama.cpp (Local / Remote)'),
    ], string='Provider', required=True)

    _sql_constraints = [
        ('code_provider_unique', 'unique(code, provider)', 'Model code must be unique per provider!')
    ]
