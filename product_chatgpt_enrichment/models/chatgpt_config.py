# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
import logging

_logger = logging.getLogger(__name__)


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
        ('ollama', 'Ollama (Local)'),
        ('llamacpp', 'Llama.cpp (Local)'),
    ], string='AI Provider', default='openai', required=True, 
       help="Choose your AI intelligence source. Cloud providers (OpenAI...) are easier to set up, Local providers (Ollama...) run on your own machine.")
    
    api_key = fields.Char(
        string='API Key / Token',
        help="The 'password' required to access your AI provider. Without this, Odoo cannot connect to the intelligence source."
    )
    
    base_url = fields.Char(
        string='Base URL',
        help='Custom API endpoint (e.g. for Ollama: http://localhost:11434)'
    )
    
    api_endpoint = fields.Char(
        string='API Endpoint Path',
        default='/v1/chat/completions',
        help='API endpoint path after base URL'
    )
    
    model_id = fields.Many2one('chatgpt.model', string='AI Model', help='Select a discovered model or enter one manually')
    
    # Kept for backward compatibility and manual overrides
    ai_model_name = fields.Char(
        string='Model Name (Manual)',
        help='Manual override if model discovery fails'
    )
    
    auto_enrich = fields.Boolean(
        string='Auto-Enrich New Products',
        default=False
    )

    use_web_search = fields.Boolean(
        string='Use Web Search',
        default=False,
        help="When enabled, the AI will perform its own quick web search (Perplexity style). Useful for generic products, but less precise than 'Deep Enrichment'."
    )

    use_deep_enrichment = fields.Boolean(
        string='Deep Enrichment (SerpApi + ScrapingBee)',
        default=False,
        help='Advanced mode: Odoo will search Google for the product and scrape content from top websites to give the AI real, up-to-date context.'
    )

    serpapi_key = fields.Char(string='SerpApi Key', help='For product and image search')
    scrapingbee_key = fields.Char(string='ScrapingBee Key', help='For bypass anti-bot and extract clean data')

    media_discovery = fields.Boolean(
        string='Discover Media',
        default=False,
        help='Odoo will analyze the AI response to find image/video URLs and automatically download/attach them to the product.'
    )
    
    max_tokens = fields.Integer(string='Max Tokens', default=2000, 
        help="Limit the length of the AI response. High values allow for longer descriptions but may increase API costs.")
    temperature = fields.Float(string='Temperature', default=0.7,
        help="Creativity level (0.0 to 1.0). 0.0 is very formal and consistent, 1.0 is creative and diverse.")
    
    prompt_ids = fields.One2many('chatgpt.product.prompt', 'config_id', string='Prompts')
    active = fields.Boolean(default=True)
    
    model_discovery_results = fields.Text(string='Discovered Models', readonly=True)

    @api.model
    def get_active_config(self):
        config = self.search([('active', '=', True)], limit=1)
        if not config:
            raise UserError(_('No active AI configuration found. Please configure the module in Settings > ChatGPT.'))
        return config

    def action_test_connection(self):
        self.ensure_one()
        try:
            res = self.call_ai_api("Hello, this is a test connection message.", max_tokens=10)
            if res:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection to %s successful!') % self.provider,
                        'type': 'success',
                    }
                }
        except Exception as e:
            raise UserError(_('Connection failed: %s') % str(e))

    def action_discover_models(self):
        self.ensure_one()
        models_data = []
        
        try:
            if self.provider == 'ollama':
                url = f"{self.base_url or 'http://localhost:11434'}/api/tags"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    models_data = [{'name': m['name'], 'code': m['name']} for m in response.json().get('models', [])]
            
            elif self.provider in ['llamacpp', 'openai', 'perplexity']:
                # OpenAI compatible /v1/models
                base = self.base_url or (
                    'https://api.openai.com' if self.provider == 'openai' else 
                    'https://api.perplexity.ai' if self.provider == 'perplexity' else 
                    'http://localhost:8080'
                )
                url = f"{base}/v1/models"
                headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    models_data = [{'name': m['id'], 'code': m['id']} for m in response.json().get('data', [])]
            
            elif self.provider == 'anthropic':
                # Anthropic /v1/models
                url = "https://api.anthropic.com/v1/models"
                headers = {
                    'x-api-key': self.api_key,
                    'anthropic-version': '2023-06-01'
                }
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    models_data = [{'name': m['display_name'], 'code': m['id']} for m in response.json().get('data', [])]
            
            elif self.provider == 'gemini':
                # Gemini models
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    models_data = [{'name': m['displayName'], 'code': m['name'].split('/')[-1]} for m in response.json().get('models', [])]

            if models_data:
                # Update chatgpt.model table
                Model = self.env['chatgpt.model']
                for m in models_data:
                    existing = Model.search([('code', '=', m['code']), ('provider', '=', self.provider)], limit=1)
                    if not existing:
                        Model.create({
                            'name': m['name'],
                            'code': m['code'],
                            'provider': self.provider
                        })
                
                self.model_discovery_results = f"Found {len(models_data)} models."
                return True
            
        except Exception as e:
            raise UserError(_("Discovery failed: %s") % str(e))
        
        raise UserError(_("No models found or discovery not supported for this provider yet."))

    def _search_with_serpapi(self, query):
        if not self.serpapi_key:
            return []
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "engine": "google",
            "api_key": self.serpapi_key,
            "num": 3
        }
        try:
            res = requests.get(url, params=params, timeout=10).json()
            return [r.get('link') for r in res.get('organic_results', []) if r.get('link')]
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
            "extract_rules": '{"content": "body"}'
        }
        try:
            res = requests.get(sb_url, params=params, timeout=30).json()
            return res.get('content', '')[:10000] # Limit size for AI
        except Exception as e:
            _logger.error("ScrapingBee error: %s", str(e))
            return ""

    def _get_model_name(self):
        """Helper to get model code from Many2one or manual field"""
        if self.model_id:
            return self.model_id.code
        return self.ai_model_name or 'gpt-4o-mini'

    def call_ai_api(self, prompt, max_tokens=None, temperature=None):
        self.ensure_one()
        if self.provider == 'openai':
            return self._call_openai(prompt, max_tokens, temperature)
        elif self.provider == 'gemini':
            return self._call_gemini(prompt, max_tokens, temperature)
        elif self.provider == 'anthropic':
            return self._call_anthropic(prompt, max_tokens, temperature)
        elif self.provider == 'perplexity':
            return self._call_perplexity(prompt, max_tokens, temperature)
        elif self.provider in ['ollama', 'llamacpp']:
            return self._call_local_ai(prompt, max_tokens, temperature)
        return False

    def _call_openai(self, prompt, max_tokens, temperature):
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        url = self.base_url or 'https://api.openai.com/v1/chat/completions'
        data = {
            'model': self._get_model_name(),
            'messages': [{'role': 'system', 'content': 'Expert product marketer. HTML output.'}, {'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens or self.max_tokens,
            'temperature': temperature or self.temperature,
        }
        try:
            res = requests.post(url, headers=headers, json=data, timeout=60).json()
            if 'error' in res:
                raise UserError(_("OpenAI Error: %s") % res['error'].get('message', 'Unknown Error'))
            return self._format_response(res['choices'][0]['message']['content'])
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from OpenAI. Please check your API Key and Model settings."))

    def _call_gemini(self, prompt, max_tokens, temperature):
        # Gemini API call (Simplified for AI Studio)
        model = self._get_model_name()
        if '/' not in model:
            model = f"models/{model}"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={self.api_key}"
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens or self.max_tokens,
                "temperature": temperature or self.temperature,
            }
        }
        try:
            res = requests.post(url, json=data, timeout=60).json()
            if 'error' in res:
                raise UserError(_("Gemini Error: %s") % res['error'].get('message', 'Unknown Error'))
            return self._format_response(res['candidates'][0]['content']['parts'][0]['text'])
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from Gemini. Please check your API Key and Model settings."))

    def _call_anthropic(self, prompt, max_tokens, temperature):
        headers = {
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        }
        url = 'https://api.anthropic.com/v1/messages'
        data = {
            'model': self._get_model_name(),
            'max_tokens': max_tokens or self.max_tokens,
            'messages': [{'role': 'user', 'content': prompt}]
        }
        try:
            res = requests.post(url, headers=headers, json=data, timeout=60).json()
            if 'error' in res:
                raise UserError(_("Anthropic Error: %s") % res['error'].get('message', 'Unknown Error'))
            return self._format_response(res['content'][0]['text'])
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from Anthropic. Please check your API Key and Model settings."))

    def _call_perplexity(self, prompt, max_tokens, temperature):
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        url = 'https://api.perplexity.ai/chat/completions'
        data = {
            'model': self._get_model_name() or 'llama-3.1-sonar-small-128k-online',
            'messages': [{'role': 'system', 'content': 'Expert researcher. HTML output.'}, {'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens or self.max_tokens,
        }
        try:
            res = requests.post(url, headers=headers, json=data, timeout=60).json()
            if 'error' in res:
                raise UserError(_("Perplexity Error: %s") % res['error'].get('message', 'Unknown Error'))
            return self._format_response(res['choices'][0]['message']['content'])
        except (KeyError, IndexError):
            raise UserError(_("Invalid response from Perplexity. Please check your API Key and Model settings."))

    def _call_local_ai(self, prompt, max_tokens, temperature):
        # Local AI via OpenAI-compatible API or Ollama native
        if self.provider == 'ollama':
            url = f"{self.base_url or 'http://localhost:11434'}/api/generate"
            data = {
                'model': self._get_model_name(),
                'prompt': prompt,
                'stream': False,
                'options': {'num_predict': max_tokens or self.max_tokens, 'temperature': temperature or self.temperature}
            }
            try:
                res = requests.post(url, json=data, timeout=120).json()
                if 'error' in res:
                    raise UserError(_("Ollama Error: %s") % res.get('error', 'Unknown Error'))
                return self._format_response(res['response'])
            except (KeyError, IndexError):
                raise UserError(_("Invalid response from Ollama. Please ensure Ollama is running and the model is downloaded."))
        else:
            # Llama.cpp with OpenAI compatible server
            return self._call_openai(prompt, max_tokens, temperature)

    def _format_response(self, content):
        if not content: return ""
        # Strip markdown and ensure clean HTML
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
        string='Prompt Template', 
        required=True,
        help="Use {product_name} as a placeholder. Add 'Answer in JSON' if you want automatic field mapping of technical specs."
    )
    
    target_field_id = fields.Many2one(
        'ir.model.fields', 
        string='Target Field',
        domain="[('model', '=', 'product.template'), ('ttype', 'in', ['char', 'text', 'html'])]",
        help="Select which Odoo field should receive the AI-generated content."
    )
    
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
        ('ollama', 'Ollama (Local)'),
        ('llamacpp', 'Llama.cpp (Local)'),
    ], string='Provider', required=True)

    _sql_constraints = [
        ('code_provider_unique', 'unique(code, provider)', 'Model code must be unique per provider!')
    ]
