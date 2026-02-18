# -*- coding: utf-8 -*-
"""Thread-safe HTTP caller for AI providers.

Used by ``ThreadPoolExecutor`` workers that cannot access the ORM.
All parameters are plain Python values (no Odoo recordsets).
"""
import json
import logging
import requests

_logger = logging.getLogger(__name__)


def call_ollama_http(base_url, model, prompt, system_prompt=None,
                     max_tokens=2000, temperature=0.7, num_ctx=4096,
                     num_gpu=99, keep_alive='10m', timeout=180):
    """Call Ollama native ``/api/chat`` without ORM.

    :returns: Response text (str) or empty string on error.
    """
    url = f"{base_url.rstrip('/')}/api/chat"
    sys_prompt = system_prompt or 'You are a helpful AI assistant.'
    data = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': prompt},
        ],
        'stream': False,
        'options': {
            'num_predict': max_tokens,
            'temperature': temperature,
            'num_ctx': num_ctx,
            'num_gpu': num_gpu,
        },
    }
    if keep_alive:
        data['keep_alive'] = keep_alive

    try:
        resp = requests.post(url, json=data, timeout=timeout)
        if resp.status_code != 200:
            _logger.error("Ollama HTTP %s — %s", resp.status_code, resp.text[:300])
            return ''
        res = resp.json()
        if 'error' in res:
            _logger.error("Ollama error: %s", res['error'])
            return ''
        msg = res.get('message', {})
        content = msg.get('content', '') if isinstance(msg, dict) else ''
        if not content:
            content = res.get('response', '')
        return _clean(content)
    except Exception as e:
        _logger.error("Ollama HTTP call failed: %s", e)
        return ''


def call_openai_http(url, api_key, model, prompt, system_prompt=None,
                     max_tokens=2000, temperature=0.7, timeout=120):
    """Call any OpenAI-compatible endpoint without ORM.

    Works for OpenAI, Perplexity, Llama.cpp, Ollama (openai mode).
    """
    sys_prompt = system_prompt or 'You are a helpful AI assistant.'
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    data = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': prompt},
        ],
        'max_tokens': max_tokens,
        'temperature': temperature,
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=timeout)
        if resp.status_code != 200:
            _logger.error("OpenAI-compat HTTP %s — %s", resp.status_code, resp.text[:300])
            return ''
        res = resp.json()
        return _clean(res['choices'][0]['message']['content'])
    except Exception as e:
        _logger.error("OpenAI-compat HTTP call failed: %s", e)
        return ''


def call_anthropic_http(api_key, model, prompt, system_prompt=None,
                        max_tokens=2000, temperature=0.7, timeout=60):
    """Call Anthropic Claude API without ORM."""
    url = 'https://api.anthropic.com/v1/messages'
    headers = {
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }
    data = {
        'model': model,
        'max_tokens': max_tokens,
        'temperature': temperature,
        'messages': [{'role': 'user', 'content': prompt}],
    }
    if system_prompt:
        data['system'] = system_prompt

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=timeout)
        if resp.status_code != 200:
            _logger.error("Anthropic HTTP %s — %s", resp.status_code, resp.text[:300])
            return ''
        res = resp.json()
        return _clean(res['content'][0]['text'])
    except Exception as e:
        _logger.error("Anthropic HTTP call failed: %s", e)
        return ''


def call_gemini_http(api_key, model, prompt, max_tokens=2000,
                     temperature=0.7, timeout=60):
    """Call Google Gemini API without ORM."""
    if '/' not in model:
        model = f"models/{model}"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"{model}:generateContent?key={api_key}"
    )
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    try:
        resp = requests.post(url, json=data, timeout=timeout)
        if resp.status_code != 200:
            _logger.error("Gemini HTTP %s — %s", resp.status_code, resp.text[:300])
            return ''
        res = resp.json()
        return _clean(res['candidates'][0]['content']['parts'][0]['text'])
    except Exception as e:
        _logger.error("Gemini HTTP call failed: %s", e)
        return ''


def _clean(content):
    """Strip markdown code fences from response."""
    if not content:
        return ''
    return content.replace('```html', '').replace('```json', '').replace('```', '').strip()
