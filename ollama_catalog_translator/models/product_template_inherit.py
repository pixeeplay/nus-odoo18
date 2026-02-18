# -*- coding: utf-8 -*-
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

LANG_MAP = {
    'en': 'English',
    'de': 'German',
    'es': 'Spanish',
    'it': 'Italian',
}


class ProductTemplateTranslation(models.Model):
    _name = 'product.template'
    _inherit = ['product.template', 'ollama.mixin']

    ai_translation_en = fields.Text(
        string='English Translation',
        help='AI-generated English translation of product name and description.',
    )
    ai_translation_de = fields.Text(
        string='German Translation',
        help='AI-generated German translation of product name and description.',
    )
    ai_translation_es = fields.Text(
        string='Spanish Translation',
        help='AI-generated Spanish translation of product name and description.',
    )
    ai_translation_it = fields.Text(
        string='Italian Translation',
        help='AI-generated Italian translation of product name and description.',
    )
    ai_translation_status = fields.Selection(
        selection=[
            ('none', 'Not Translated'),
            ('partial', 'Partially Translated'),
            ('complete', 'Fully Translated'),
        ],
        string='Translation Status',
        default='none',
        tracking=True,
    )
    ai_translation_date = fields.Datetime(
        string='Last Translation Date',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Translation logic
    # ------------------------------------------------------------------
    def action_translate_product(self, target_langs=None):
        """Translate the product name and description to the target languages.

        :param target_langs: list of language codes, e.g. ['en', 'de', 'es'].
                             Defaults to ['en', 'de', 'es'] if not provided.
        :returns: notification action dict
        """
        self.ensure_one()

        if target_langs is None:
            target_langs = ['en', 'de', 'es']

        # Build source text
        product_name = self.name or ''
        product_desc = self.description_sale or self.description or ''

        if not product_name:
            raise UserError(_('Product name is empty. Cannot translate.'))

        # Build the language list for the prompt
        lang_labels = []
        for lc in target_langs:
            label = LANG_MAP.get(lc, lc.upper())
            lang_labels.append(f'{lc} ({label})')

        lang_list_str = ', '.join(lang_labels)

        system_prompt = (
            "You are a professional product catalog translator. "
            "You translate product information accurately and naturally. "
            "Always respond with valid JSON only, no extra text."
        )

        prompt = (
            f"Translate the following product information into these languages: {lang_list_str}.\n\n"
            f"Product Name: {product_name}\n"
        )
        if product_desc:
            prompt += f"Product Description: {product_desc}\n"

        prompt += (
            "\nReturn a JSON object with one key per language code. "
            "Each value must be an object with 'name' and 'description' keys.\n"
            "Example format:\n"
            '{\n'
            '  "en": {"name": "...", "description": "..."},\n'
            '  "de": {"name": "...", "description": "..."}\n'
            '}\n'
            "If there is no description to translate, set description to an empty string.\n"
            "Respond ONLY with the JSON, no markdown, no extra text."
        )

        # Call the AI
        raw_response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            max_tokens=3000,
            temperature=0.3,
            log_model=self._name,
            log_res_id=self.id,
        )

        if not raw_response:
            raise UserError(_('AI did not return a response. Check your AI configuration.'))

        # Parse
        parsed = self._parse_json_response(raw_response)
        if not parsed or not isinstance(parsed, dict):
            _logger.error(
                "Failed to parse translation JSON for product %s: %s",
                self.id, raw_response[:500],
            )
            raise UserError(_(
                'Could not parse AI response as JSON. Raw response:\n%s'
            ) % raw_response[:500])

        # Write translations
        vals = {}
        translated_count = 0
        field_map = {
            'en': 'ai_translation_en',
            'de': 'ai_translation_de',
            'es': 'ai_translation_es',
            'it': 'ai_translation_it',
        }

        for lc in target_langs:
            field_name = field_map.get(lc)
            if not field_name:
                continue

            lang_data = parsed.get(lc, {})
            if not isinstance(lang_data, dict):
                continue

            translated_name = lang_data.get('name', '')
            translated_desc = lang_data.get('description', '')

            # Build display text
            parts = []
            if translated_name:
                parts.append(f"Name: {translated_name}")
            if translated_desc:
                parts.append(f"Description: {translated_desc}")

            if parts:
                vals[field_name] = '\n'.join(parts)
                translated_count += 1

        # Determine status
        if translated_count == 0:
            vals['ai_translation_status'] = 'none'
        elif translated_count >= len(target_langs):
            vals['ai_translation_status'] = 'complete'
        else:
            vals['ai_translation_status'] = 'partial'

        vals['ai_translation_date'] = fields.Datetime.now()
        self.write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Translation Complete'),
                'message': _('Product "%s" translated to %d language(s).') % (
                    product_name, translated_count,
                ),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_translate_product_button(self):
        """Button action: translate to default languages (en, de, es)."""
        return self.action_translate_product()
