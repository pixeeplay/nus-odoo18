# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

PROVIDER_DEFAULTS = {
    'openai': {'base_url': 'https://api.openai.com', 'endpoint': '/v1/chat/completions', 'model': 'gpt-4o-mini'},
    'gemini': {'base_url': 'https://generativelanguage.googleapis.com', 'endpoint': '', 'model': 'gemini-2.0-flash'},
    'anthropic': {'base_url': 'https://api.anthropic.com', 'endpoint': '/v1/messages', 'model': 'claude-sonnet-4-5-20250929'},
    'perplexity': {'base_url': 'https://api.perplexity.ai', 'endpoint': '/chat/completions', 'model': 'llama-3.1-sonar-small-128k-online'},
    'ollama': {'base_url': 'http://localhost:11434', 'endpoint': '/api/chat', 'model': 'llama3.2'},
    'llamacpp': {'base_url': 'http://localhost:8080', 'endpoint': '/v1/chat/completions', 'model': 'default'},
}


class OllamaConfig(models.Model):
    _name = 'ollama.config'
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
    ], string='AI Provider', default='ollama', required=True)

    api_key = fields.Char(
        string='API Key / Token',
        help="API key for cloud providers. For Ollama local, leave empty.",
    )
    base_url = fields.Char(
        string='Base URL',
        help="Server address. Examples:\n"
             "- Ollama local: http://localhost:11434\n"
             "- Ollama remote: http://192.168.x.x:11434\n"
             "- OpenAI: https://api.openai.com",
    )
    api_endpoint = fields.Char(
        string='API Endpoint Path',
        help="Path appended to Base URL. Auto-set per provider.",
    )
    ollama_api_mode = fields.Selection([
        ('native', 'Ollama Native (/api/chat)'),
        ('openai', 'OpenAI Compatible (/v1/chat/completions)'),
    ], string='Ollama API Mode', default='native')

    ollama_web_search = fields.Boolean(
        string='Ollama Web Search', default=False,
        help="Enable Ollama's built-in web search (requires cloud key).",
    )
    ollama_cloud_key = fields.Char(
        string='Ollama Cloud Key',
        help="API key for Ollama cloud (web search). Get one at ollama.com/settings/keys",
    )

    model_id = fields.Many2one('ollama.model', string='AI Model')
    ai_model_name = fields.Char(
        string='Model Name (Manual)',
        help='Examples: llama3.2, gpt-4o-mini, claude-sonnet-4-5-20250929',
    )

    max_tokens = fields.Integer(string='Max Tokens', default=2000)
    temperature = fields.Float(string='Temperature', default=0.7)
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(string='Default Config', default=False)

    # Ollama tuning
    ollama_request_timeout = fields.Integer(
        string='Request Timeout (s)', default=180,
        help="Timeout per API call. Increase for large models.",
    )
    ollama_num_ctx = fields.Integer(
        string='Context Window', default=4096,
        help="Context window size (num_ctx). Common: 2048, 4096, 8192.",
    )
    ollama_num_gpu = fields.Integer(
        string='GPU Layers', default=99,
        help="Layers on GPU. 99 = all (fastest). 0 = CPU only.",
    )
    ollama_keep_alive = fields.Char(
        string='Keep Alive', default='10m',
        help="How long model stays in memory. Examples: 5m, 10m, 1h, 0.",
    )
    ollama_parallel_workers = fields.Integer(
        string='Parallel Workers', default=2,
        help="Parallel threads for batch AI calls.",
    )

    last_test_result = fields.Text(string='Last Test Result', readonly=True)

    # -------------------------------------------------------
    # Computed helpers
    # -------------------------------------------------------
    @api.onchange('provider')
    def _onchange_provider(self):
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
        """Return the default config, or the first active one."""
        config = self.search([('is_default', '=', True), ('active', '=', True)], limit=1)
        if not config:
            config = self.search([('active', '=', True)], limit=1)
        if not config:
            raise UserError(_('No active AI configuration found. Go to Settings > Ollama AI.'))
        return config

    def action_set_default(self):
        """Set this config as the default (unset all others)."""
        self.ensure_one()
        self.search([('is_default', '=', True)]).write({'is_default': False})
        self.is_default = True
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Default Configuration'),
                'message': _('"%s" is now the default AI provider.') % self.name,
                'type': 'success',
                'sticky': False,
            }
        }

    def get_provider_display(self):
        return dict(self._fields['provider'].selection).get(self.provider, self.provider)

    # -------------------------------------------------------
    # URL helpers
    # -------------------------------------------------------
    def _get_base_url(self):
        """Get the base URL (scheme + host + port only, no API path)."""
        url = (self.base_url or '').strip().rstrip('/')
        if not url:
            return PROVIDER_DEFAULTS.get(self.provider, {}).get('base_url', 'http://localhost:11434')
        for suffix in ['/v1/chat/completions', '/v1/messages', '/chat/completions',
                       '/api/chat', '/api/generate', '/api/tags', '/api']:
            if url.endswith(suffix):
                url = url[:-len(suffix)]
                break
        return url.rstrip('/')

    def _get_full_url(self, endpoint_override=None):
        """Build the full API URL from base_url + endpoint."""
        base = self._get_base_url()
        endpoint = endpoint_override or self.api_endpoint or ''
        if endpoint.startswith('http'):
            return endpoint
        if endpoint and not endpoint.startswith('/'):
            endpoint = '/' + endpoint
        if endpoint and base.endswith(endpoint):
            return base
        return base + endpoint

    def _get_model_name(self):
        """Get model name: manual field > Many2one > provider default."""
        if self.ai_model_name:
            return self.ai_model_name
        if self.model_id:
            return self.model_id.code
        return PROVIDER_DEFAULTS.get(self.provider, {}).get('model', 'llama3.2')

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
    # Connection test
    # -------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()
        try:
            base = self._get_base_url()
            if self.provider == 'ollama':
                test_url = f"{base}/api/tags"
                resp = requests.get(test_url, timeout=10)
                if resp.status_code != 200:
                    raise UserError(_(
                        "Cannot reach Ollama at %s\nStatus: %s\nResponse: %s"
                    ) % (test_url, resp.status_code, resp.text[:300]))
                models_list = resp.json().get('models', [])
                model_names = [m.get('name', '?') for m in models_list]
                _logger.info("Ollama OK - %d models: %s", len(models_list), model_names)

            res = self.call_ai_api("Say 'Hello from Odoo!' in one sentence.", max_tokens=50)
            self.last_test_result = f"OK: {res[:200]}" if res else "OK (empty)"

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection OK!'),
                    'message': _('Provider: %s | Model: %s | Response: %s') % (
                        self.provider, self._get_model_name(), (res or '')[:100]),
                    'type': 'success',
                    'sticky': True,
                }
            }
        except UserError:
            raise
        except requests.exceptions.ConnectionError as e:
            raise UserError(_(
                "Cannot connect to %s at %s\nError: %s"
            ) % (self.provider, self._get_base_url(), str(e)[:300]))
        except Exception as e:
            raise UserError(_('Connection failed: %s') % str(e))

    # -------------------------------------------------------
    # Model discovery
    # -------------------------------------------------------
    def action_discover_models(self):
        self.ensure_one()
        models_data = []
        try:
            if self.provider == 'ollama':
                url = f"{self._get_base_url()}/api/tags"
                response = requests.get(url, timeout=10)
                if response.status_code != 200:
                    raise UserError(_("Ollama discovery failed at %s") % url)
                for m in response.json().get('models', []):
                    name = m.get('name', '')
                    size = m.get('size', 0)
                    size_gb = round(size / (1024**3), 1) if size else 0
                    label = f"{name} ({size_gb}GB)" if size_gb else name
                    models_data.append({'name': label, 'code': name})
            elif self.provider == 'llamacpp':
                url = f"{self._get_base_url()}/v1/models"
                headers = self._get_headers()
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    for m in response.json().get('data', []):
                        mid = m.get('id', '')
                        models_data.append({'name': mid, 'code': mid})
            elif self.provider == 'openai':
                url = 'https://api.openai.com/v1/models'
                headers = self._get_headers()
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    for m in response.json().get('data', []):
                        mid = m.get('id', '')
                        if 'gpt' in mid or 'o1' in mid or 'o3' in mid:
                            models_data.append({'name': mid, 'code': mid})

            if not models_data:
                raise UserError(_("No models found."))

            OllamaModel = self.env['ollama.model']
            created = 0
            for md in models_data:
                existing = OllamaModel.search([
                    ('code', '=', md['code']), ('provider', '=', self.provider)
                ], limit=1)
                if not existing:
                    OllamaModel.create({
                        'name': md['name'], 'code': md['code'], 'provider': self.provider,
                    })
                    created += 1

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Models Discovered'),
                    'message': _('%d models found, %d new.') % (len(models_data), created),
                    'type': 'success', 'sticky': False,
                }
            }
        except UserError:
            raise
        except Exception as e:
            raise UserError(_("Discovery failed: %s") % str(e))

    # -------------------------------------------------------
    # Ollama web search
    # -------------------------------------------------------
    def _ollama_web_search(self, query, max_results=5):
        """Use Ollama's cloud web search API."""
        cloud_key = self.ollama_cloud_key or self.api_key
        if not cloud_key:
            return []
        url = "https://ollama.com/api/web_search"
        headers = {'Authorization': f'Bearer {cloud_key}', 'Content-Type': 'application/json'}
        data = {'query': query, 'max_results': min(max_results, 10)}
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=15)
            if resp.status_code == 200:
                results = resp.json()
                return results if isinstance(results, list) else results.get('results', [])
        except Exception as e:
            _logger.error("Ollama web search error: %s", e)
        return []

    # -------------------------------------------------------
    # Main AI dispatcher
    # -------------------------------------------------------
    def call_ai_api(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
        """Unified AI call dispatcher. Works with all providers.

        :param prompt: User message / prompt text
        :param system_prompt: Optional system prompt (default: generic assistant)
        :param max_tokens: Override max tokens
        :param temperature: Override temperature
        :returns: AI response text (str)
        """
        self.ensure_one()
        effective_model = self._get_model_name()
        _logger.info("AI call [%s] model=%s url=%s", self.provider, effective_model, self._get_base_url())

        # Ollama web search: prepend web context
        if self.provider == 'ollama' and self.ollama_web_search:
            search_query = prompt[:150].replace('\n', ' ')
            web_results = self._ollama_web_search(search_query)
            if web_results:
                parts = []
                for r in web_results[:5]:
                    title = r.get('title', '')
                    content = r.get('content', r.get('snippet', ''))[:500]
                    parts.append(f"[{title}]: {content}")
                prompt = f"Web context:\n\n{''.join(parts)}\n\nQuestion:\n{prompt}"

        if self.provider == 'openai':
            return self._call_openai_compatible(prompt, system_prompt, max_tokens, temperature)
        elif self.provider == 'gemini':
            return self._call_gemini(prompt, max_tokens, temperature)
        elif self.provider == 'anthropic':
            return self._call_anthropic(prompt, system_prompt, max_tokens, temperature)
        elif self.provider == 'perplexity':
            return self._call_openai_compatible(
                prompt, system_prompt, max_tokens, temperature,
                base_url='https://api.perplexity.ai', endpoint='/chat/completions')
        elif self.provider == 'ollama':
            return self._call_ollama(prompt, system_prompt, max_tokens, temperature)
        elif self.provider == 'llamacpp':
            return self._call_openai_compatible(prompt, system_prompt, max_tokens, temperature)
        return ''

    # -------------------------------------------------------
    # Provider implementations
    # -------------------------------------------------------
    def _call_ollama(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
        """Call Ollama using native /api/chat or OpenAI-compatible mode."""
        if self.ollama_api_mode == 'openai':
            return self._call_openai_compatible(prompt, system_prompt, max_tokens, temperature)

        base = self._get_base_url()
        url = f"{base}/api/chat"
        model = self._get_model_name()
        sys_prompt = system_prompt or 'You are a helpful AI assistant.'

        data = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': sys_prompt},
                {'role': 'user', 'content': prompt},
            ],
            'stream': False,
            'options': {
                'num_predict': max_tokens or self.max_tokens,
                'temperature': temperature if temperature is not None else self.temperature,
                'num_ctx': self.ollama_num_ctx or 4096,
                'num_gpu': self.ollama_num_gpu if self.ollama_num_gpu is not None else 99,
            },
        }
        if self.ollama_keep_alive:
            data['keep_alive'] = self.ollama_keep_alive

        timeout = self.ollama_request_timeout or 180
        try:
            resp = requests.post(url, json=data, timeout=timeout)
            if resp.status_code != 200:
                raise UserError(_(
                    "Ollama error.\nURL: %s\nStatus: %s\nResponse: %s"
                ) % (url, resp.status_code, resp.text[:500]))
            res = resp.json()
            if 'error' in res:
                raise UserError(_("Ollama Error: %s") % res.get('error', 'Unknown'))
            msg = res.get('message', {})
            content = msg.get('content', '') if isinstance(msg, dict) else ''
            if not content:
                content = res.get('response', '')
            return self._format_response(content)
        except UserError:
            raise
        except requests.exceptions.ConnectionError:
            raise UserError(_(
                "Cannot connect to Ollama at %s\nMake sure Ollama is running (ollama serve)."
            ) % base)
        except Exception as e:
            raise UserError(_("Ollama call failed: %s") % str(e))

    def _call_openai_compatible(self, prompt, system_prompt=None, max_tokens=None,
                                temperature=None, base_url=None, endpoint=None):
        """Call any OpenAI-compatible /v1/chat/completions endpoint."""
        if base_url:
            url = base_url.rstrip('/') + (endpoint or '/v1/chat/completions')
        else:
            url = self._get_full_url(endpoint)

        sys_prompt = system_prompt or 'You are a helpful AI assistant.'
        headers = self._get_headers()
        data = {
            'model': self._get_model_name(),
            'messages': [
                {'role': 'system', 'content': sys_prompt},
                {'role': 'user', 'content': prompt},
            ],
            'max_tokens': max_tokens or self.max_tokens,
            'temperature': temperature if temperature is not None else self.temperature,
        }
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=120)
            if resp.status_code != 200:
                raise UserError(_(
                    "%s API error.\nURL: %s\nStatus: %s\nResponse: %s"
                ) % (self.provider, url, resp.status_code, resp.text[:500]))
            res = resp.json()
            if 'error' in res:
                raise UserError(_("%s Error: %s") % (self.provider, res['error'].get('message', str(res['error']))))
            return self._format_response(res['choices'][0]['message']['content'])
        except UserError:
            raise
        except requests.exceptions.ConnectionError:
            raise UserError(_("Cannot connect to %s at %s") % (self.provider, url))
        except (KeyError, IndexError) as e:
            raise UserError(_("Invalid response from %s: %s") % (self.provider, str(e)))

    def _call_gemini(self, prompt, max_tokens=None, temperature=None):
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
                raise UserError(_("Gemini error. Status: %s") % resp.status_code)
            res = resp.json()
            return self._format_response(res['candidates'][0]['content']['parts'][0]['text'])
        except UserError:
            raise
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from Gemini."))

    def _call_anthropic(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
        headers = self._get_headers()
        url = 'https://api.anthropic.com/v1/messages'
        data = {
            'model': self._get_model_name(),
            'max_tokens': max_tokens or self.max_tokens,
            'temperature': temperature if temperature is not None else self.temperature,
            'messages': [{'role': 'user', 'content': prompt}],
        }
        if system_prompt:
            data['system'] = system_prompt
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            if resp.status_code != 200:
                raise UserError(_("Anthropic error. Status: %s") % resp.status_code)
            res = resp.json()
            return self._format_response(res['content'][0]['text'])
        except UserError:
            raise
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from Anthropic."))

    def _format_response(self, content):
        if not content:
            return ""
        return content.replace('```html', '').replace('```json', '').replace('```', '').strip()


class OllamaModel(models.Model):
    _name = 'ollama.model'
    _description = 'AI Model'
    _order = 'provider, name'

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    provider = fields.Selection([
        ('openai', 'OpenAI'),
        ('gemini', 'Google Gemini'),
        ('anthropic', 'Anthropic Claude'),
        ('perplexity', 'Perplexity'),
        ('ollama', 'Ollama'),
        ('llamacpp', 'Llama.cpp'),
    ], string='Provider', required=True)

    _sql_constraints = [
        ('code_provider_unique', 'unique(code, provider)', 'Model code must be unique per provider!')
    ]
