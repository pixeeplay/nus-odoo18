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
    ], string='AI Provider', default='openai', required=True)
    
    api_key = fields.Char(
        string='API Key / Token',
        help='Your API key or token for the selected provider.'
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
    
    ai_model_name = fields.Char(
        string='Model Name',
        default='gpt-4o-mini',
        help='Enter the model name (e.g. gpt-4o, claude-3-5-sonnet, or the local model name)'
    )
    
    auto_enrich = fields.Boolean(
        string='Auto-Enrich New Products',
        default=False
    )

    use_web_search = fields.Boolean(
        string='Use Web Search',
        default=False,
        help='If enabled, Perplexity or specific search-enabled models will be used.'
    )

    use_deep_enrichment = fields.Boolean(
        string='Deep Enrichment (SerpApi + ScrapingBee)',
        default=False,
        help='Use SerpApi to find products and ScrapingBee to extract deep content before AI processing.'
    )

    serpapi_key = fields.Char(string='SerpApi Key', help='For product and image search')
    scrapingbee_key = fields.Char(string='ScrapingBee Key', help='For bypass anti-bot and extract clean data')

    media_discovery = fields.Boolean(
        string='Discover Media',
        default=False,
        help='Try to find official product image and video URLs'
    )
    
    max_tokens = fields.Integer(string='Max Tokens', default=2000)
    temperature = fields.Float(string='Temperature', default=0.7)
    
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
        if self.provider == 'ollama':
            url = f"{self.base_url or 'http://localhost:11434'}/api/tags"
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    models = [m['name'] for m in response.json().get('models', [])]
                    self.model_discovery_results = "\n".join(models)
                    return True
            except Exception as e:
                raise UserError(_("Could not reach Ollama: %s") % str(e))
        elif self.provider in ['llamacpp', 'openai']:
            # Llama.cpp usually supports /v1/models if using server
            url = f"{self.base_url or 'http://localhost:8080'}/v1/models"
            try:
                headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    models = [m['id'] for m in response.json().get('data', [])]
                    self.model_discovery_results = "\n".join(models)
                    return True
            except Exception as e:
                raise UserError(_("Could not reach server: %s") % str(e))
        
        raise UserError(_("Model discovery not implemented for this provider yet."))

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
            'model': self.ai_model_name,
            'messages': [{'role': 'system', 'content': 'Expert product marketer. HTML output.'}, {'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens or self.max_tokens,
            'temperature': temperature or self.temperature,
        }
        res = requests.post(url, headers=headers, json=data, timeout=60).json()
        return self._format_response(res['choices'][0]['message']['content'])

    def _call_gemini(self, prompt, max_tokens, temperature):
        # Gemini API call (Simplified for AI Studio)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.ai_model_name}:generateContent?key={self.api_key}"
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens or self.max_tokens,
                "temperature": temperature or self.temperature,
            }
        }
        res = requests.post(url, json=data, timeout=60).json()
        return self._format_response(res['candidates'][0]['content']['parts'][0]['text'])

    def _call_anthropic(self, prompt, max_tokens, temperature):
        headers = {
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        }
        url = 'https://api.anthropic.com/v1/messages'
        data = {
            'model': self.ai_model_name,
            'max_tokens': max_tokens or self.max_tokens,
            'messages': [{'role': 'user', 'content': prompt}]
        }
        res = requests.post(url, headers=headers, json=data, timeout=60).json()
        return self._format_response(res['content'][0]['text'])

    def _call_perplexity(self, prompt, max_tokens, temperature):
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        url = 'https://api.perplexity.ai/chat/completions'
        data = {
            'model': self.ai_model_name or 'llama-3.1-sonar-small-128k-online',
            'messages': [{'role': 'system', 'content': 'Expert researcher. HTML output.'}, {'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens or self.max_tokens,
        }
        res = requests.post(url, headers=headers, json=data, timeout=60).json()
        return self._format_response(res['choices'][0]['message']['content'])

    def _call_local_ai(self, prompt, max_tokens, temperature):
        # Local AI via OpenAI-compatible API or Ollama native
        if self.provider == 'ollama':
            url = f"{self.base_url or 'http://localhost:11434'}/api/generate"
            data = {
                'model': self.ai_model_name,
                'prompt': prompt,
                'stream': False,
                'options': {'num_predict': max_tokens or self.max_tokens, 'temperature': temperature or self.temperature}
            }
            res = requests.post(url, json=data, timeout=120).json()
            return self._format_response(res['response'])
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
    
    prompt_template = fields.Text(string='Prompt Template', required=True)
    
    target_field_id = fields.Many2one(
        'ir.model.fields', 
        string='Target Field',
        domain="[('model', '=', 'product.template'), ('ttype', 'in', ['char', 'text', 'html'])]"
    )
    
    language = fields.Selection([
        ('fr_FR', 'French'),
        ('en_US', 'English'),
        ('de_DE', 'German'),
        ('es_ES', 'Spanish'),
        ('it_IT', 'Italian'),
    ], string='Language', default='fr_FR')

    active = fields.Boolean(default=True)
