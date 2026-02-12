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
        ('gpt-4', 'GPT-4 (Most Capable)'),
        ('gpt-4-turbo', 'GPT-4 Turbo (Fast & Capable)'),
        ('gpt-3.5-turbo', 'GPT-3.5 Turbo (Fast & Economical)'),
    ], string='Model', default='gpt-3.5-turbo', required=True,
        help='Select the ChatGPT model to use for enrichment')
    
    auto_enrich = fields.Boolean(
        string='Auto-Enrich New Products',
        default=True,
        help='Automatically enrich products when they are created'
    )
    
    max_tokens = fields.Integer(
        string='Max Tokens',
        default=500,
        help='Maximum number of tokens in the response'
    )
    
    temperature = fields.Float(
        string='Temperature',
        default=0.7,
        help='Controls randomness: 0 is focused, 1 is creative'
    )
    
    prompt_template = fields.Text(
        string='Prompt Template',
        default="""You are a product marketing expert. Based on the product name "{product_name}", generate a high-quality, SEO-optimized product description in HTML format.

The content should include:
1. A catchy title (h2)
2. A compelling introduction (p)
3. Main features and benefits (ul/li)
4. A concluding paragraph (p)

Product Name: {product_name}

Please return ONLY the HTML content, without any extra text or markdown code blocks.""",
        help='Template for the ChatGPT prompt. Use {product_name} as placeholder.'
    )
    
    active = fields.Boolean(default=True)

    @api.model
    def get_active_config(self):
        """Get the active configuration"""
        config = self.search([('active', '=', True)], limit=1)
        if not config:
            raise UserError(_('No active ChatGPT configuration found. Please configure the module in Settings > Technical > ChatGPT Configuration.'))
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

    def call_chatgpt_api(self, prompt):
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
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': self.max_tokens,
                'temperature': self.temperature,
            }
            
            _logger.info('Calling ChatGPT API with model: %s', self.model_name)
            
            response = requests.post(
                self.api_endpoint,
                headers=headers,
                json=data,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                _logger.info('ChatGPT API call successful')
                return content
            else:
                error_msg = f'API Error: {response.status_code} - {response.text}'
                _logger.error(error_msg)
                raise UserError(_(error_msg))
                
        except requests.exceptions.RequestException as e:
            error_msg = f'Connection failed: {str(e)}'
            _logger.error(error_msg)
            raise UserError(_(error_msg))
