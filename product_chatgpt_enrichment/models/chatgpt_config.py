# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
import logging

_logger = logging.getLogger(__name__)


class ChatGPTConfig(models.Model):
    _name = 'chatgpt.config'
    _description = 'ChatGPT Configuration'
    _rec_name = 'model_name'

    api_key = fields.Char(
        string='OpenAI API Key',
        required=True,
        help='Your OpenAI API key from https://platform.openai.com/api-keys'
    )
    api_endpoint = fields.Char(
        string='API Endpoint',
        default='https://api.openai.com/v1/chat/completions',
        required=True,
        help='OpenAI API endpoint URL'
    )
    model_name = fields.Selection([
        ('gpt-4o', 'GPT-4o (Most Capable)'),
        ('gpt-4o-mini', 'GPT-4o Mini (Fast & Economical)'),
        ('gpt-4-turbo', 'GPT-4 Turbo'),
        ('gpt-3.5-turbo', 'GPT-3.5 Turbo (Legacy)'),
    ], string='Model', default='gpt-4o-mini', required=True,
        help='Select the ChatGPT model to use for enrichment')
    
    auto_enrich = fields.Boolean(
        string='Auto-Enrich New Products',
        default=True,
        help='Automatically enrich products when they are created'
    )

    use_web_search = fields.Boolean(
        string='Use Web Search',
        default=False,
        help='Allow ChatGPT to search the web for up-to-date information (if model supports it)'
    )

    media_discovery = fields.Boolean(
        string='Discover Media',
        default=False,
        help='Try to find official product image and video URLs'
    )
    
    max_tokens = fields.Integer(
        string='Max Tokens',
        default=1000,
        help='Maximum number of tokens in the response'
    )
    
    temperature = fields.Float(
        string='Temperature',
        default=0.7,
        help='Controls randomness: 0 is focused, 1 is creative'
    )
    
    prompt_ids = fields.One2many(
        'chatgpt.product.prompt', 'config_id', 
        string='Prompts'
    )
    
    active = fields.Boolean(default=True)

    @api.model
    def get_active_config(self):
        """Get the active configuration"""
        config = self.search([('active', '=', True)], limit=1)
        if not config:
            raise UserError(_('No active ChatGPT configuration found. Please configure the module in Settings > ChatGPT.'))
        return config

    def action_test_connection(self):
        """Test the API connection"""
        self.ensure_one()
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            }
            
            data = {
                'model': self.model_name,
                'messages': [
                    {'role': 'user', 'content': 'Hello, this is a test message.'}
                ],
                'max_tokens': 50,
            }
            
            response = requests.post(
                self.api_endpoint,
                headers=headers,
                json=data,
                timeout=10
            )
            
            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection to ChatGPT API successful!'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_('API Error: %s - %s') % (response.status_code, response.text))
                
        except requests.exceptions.RequestException as e:
            raise UserError(_('Connection failed: %s') % str(e))

    def call_chatgpt_api(self, prompt, max_tokens=None, temperature=None):
        """Call the ChatGPT API with the given prompt"""
        self.ensure_one()
        
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            }
            
            data = {
                'model': self.model_name,
                'messages': [
                    {'role': 'system', 'content': 'You are a product marketing expert. Always provide output in clean HTML without markdown code blocks unless requested.'},
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': max_tokens or self.max_tokens,
                'temperature': temperature or self.temperature,
            }
            
            _logger.info('Calling ChatGPT API with model: %s', self.model_name)
            
            response = requests.post(
                self.api_endpoint,
                headers=headers,
                json=data,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                # Strip markdown code blocks if present
                if content.startswith('```html'):
                    content = content[7:]
                if content.startswith('```'):
                    content = content[3:]
                if content.endswith('```'):
                    content = content[:-3]
                _logger.info('ChatGPT API call successful')
                return content.strip()
            else:
                error_msg = f'API Error: {response.status_code} - {response.text}'
                _logger.error(error_msg)
                raise UserError(_(error_msg))
                
        except requests.exceptions.RequestException as e:
            error_msg = f'Connection failed: {str(e)}'
            _logger.error(error_msg)
            raise UserError(_(error_msg))


class ChatGPTProductPrompt(models.Model):
    _name = 'chatgpt.product.prompt'
    _description = 'ChatGPT Product Prompt'
    _order = 'sequence, id'

    name = fields.Char(string='Name', required=True, help="e.g. Long Description, Technical Specs")
    sequence = fields.Integer(default=10)
    config_id = fields.Many2one('chatgpt.config', string='Configuration', ondelete='cascade')
    
    prompt_template = fields.Text(
        string='Prompt Template',
        required=True,
        help='Template for the ChatGPT prompt. Use {product_name} as placeholder.'
    )
    
    target_field = fields.Selection([
        ('website_description', 'Website Description (HTML)'),
        ('description_sale', 'Sales Description (Text)'),
        ('description', 'Internal Notes (Text)'),
        ('chatgpt_content', 'AI Enrichment Log (HTML)'),
    ], string='Target Field', default='website_description', required=True)
    
    language = fields.Selection([
        ('fr_FR', 'French'),
        ('en_US', 'English'),
        ('de_DE', 'German'),
        ('es_ES', 'Spanish'),
        ('it_IT', 'Italian'),
    ], string='Language', default='fr_FR')

    active = fields.Boolean(default=True)
