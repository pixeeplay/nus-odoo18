# -*- coding: utf-8 -*-
import json
import logging
import re

from odoo import models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OllamaMixin(models.AbstractModel):
    """Mixin providing AI call helpers for all downstream modules.

    Inherit this mixin in any model that needs AI capabilities::

        class MyModel(models.Model):
            _inherit = ['ollama.mixin']
    """
    _name = 'ollama.mixin'
    _description = 'Ollama AI Mixin'

    # --------------------------------------------------
    # Config helpers
    # --------------------------------------------------
    @api.model
    def _get_ollama_config(self):
        """Return the active ``ollama.config`` record."""
        return self.env['ollama.config'].get_active_config()

    # --------------------------------------------------
    # Safe AI call
    # --------------------------------------------------
    def _call_ollama_safe(self, prompt, system_prompt=None, max_tokens=None,
                          temperature=None, config=None, log_model=None,
                          log_res_id=None):
        """Call the AI API with error handling and optional logging.

        :param prompt: User prompt text
        :param system_prompt: Optional system prompt
        :param max_tokens: Override max tokens
        :param temperature: Override temperature
        :param config: Specific ``ollama.config`` record (default: active)
        :param log_model: ``_name`` of the calling model for logging
        :param log_res_id: Record ID for logging
        :returns: AI response text, or empty string on error
        """
        if not config:
            try:
                config = self._get_ollama_config()
            except UserError:
                _logger.warning("No active AI config found.")
                return ''

        try:
            result = config.call_ai_api(
                prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except UserError as e:
            _logger.error("AI call error: %s", e)
            result = ''
        except Exception as e:
            _logger.exception("Unexpected AI error: %s", e)
            result = ''

        # Log the call
        if log_model or log_res_id:
            try:
                self.env['ollama.log'].sudo().create({
                    'config_id': config.id,
                    'provider': config.provider,
                    'model_name': config._get_model_name(),
                    'prompt_preview': (prompt or '')[:500],
                    'response_preview': (result or '')[:500],
                    'res_model': log_model or '',
                    'res_id': log_res_id or 0,
                    'status': 'success' if result else 'error',
                })
            except Exception:
                pass  # logging should never break the main flow

        return result or ''

    # --------------------------------------------------
    # JSON parsing
    # --------------------------------------------------
    @staticmethod
    def _parse_json_response(text):
        """Extract JSON from AI response text.

        Handles: raw JSON, markdown code blocks, mixed text with JSON inside.

        :param text: Raw AI response string
        :returns: Parsed dict/list, or ``None`` on failure
        """
        if not text:
            return None

        # Try direct parse first
        text = text.strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try extracting from markdown code blocks
        patterns = [
            r'```json\s*([\s\S]*?)\s*```',
            r'```\s*([\s\S]*?)\s*```',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except (json.JSONDecodeError, ValueError):
                    continue

        # Try finding first { ... } or [ ... ] block
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = text.find(start_char)
            if start == -1:
                continue
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except (json.JSONDecodeError, ValueError):
                            break

        _logger.warning("Could not parse JSON from AI response: %s", text[:200])
        return None
