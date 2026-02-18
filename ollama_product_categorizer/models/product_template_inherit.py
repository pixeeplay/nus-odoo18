# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = ['product.template', 'ollama.mixin']
    _name = 'product.template'

    # ------------------------------------------------------------------
    # AI Category Fields
    # ------------------------------------------------------------------
    ai_category_suggestion = fields.Char(
        string='AI Category Suggestion',
        help='Category path suggested by the AI engine.',
        tracking=True,
    )
    ai_category_confidence = fields.Float(
        string='AI Confidence (%)',
        digits=(5, 1),
        help='Confidence score from 0 to 100 percent.',
    )
    ai_category_taxonomy = fields.Selection([
        ('google', 'Google Product Taxonomy'),
        ('amazon', 'Amazon Browse Node'),
        ('custom', 'Custom Taxonomy'),
    ], string='Taxonomy', default='google',
        help='Category taxonomy used for AI categorization.',
    )
    ai_category_path = fields.Char(
        string='Full Category Path',
        help='Complete hierarchical path returned by the AI.',
    )
    ai_category_date = fields.Datetime(
        string='Last Categorized',
        help='Date and time of the last AI categorization.',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_categorize_product(self):
        """Ask the AI to categorize this product."""
        self.ensure_one()
        config = self._get_ollama_config()

        # Build taxonomy label
        taxonomy_labels = {
            'google': 'Google Product Taxonomy',
            'amazon': 'Amazon Browse Node Tree',
            'custom': 'a general e-commerce category tree',
        }
        taxonomy = taxonomy_labels.get(self.ai_category_taxonomy, 'Google Product Taxonomy')

        # Gather product info
        name = self.name or ''
        description = ''
        if self.description_sale:
            description = self.description_sale
        elif self.description:
            description = self.description
        price = self.list_price or 0.0

        system_prompt = (
            "You are an expert product categorization engine. "
            "You classify products into standardized category taxonomies. "
            "Always respond with valid JSON only, no extra text."
        )

        prompt = (
            f"Categorize the following product using the {taxonomy}.\n\n"
            f"Product name: {name}\n"
            f"Description: {description[:500] if description else 'N/A'}\n"
            f"Price: {price}\n\n"
            "Respond ONLY with a JSON object in this exact format:\n"
            '{\n'
            '  "category_path": "Top Level > Sub Level > Category",\n'
            '  "confidence": 85,\n'
            '  "reasoning": "Brief explanation of why this category was chosen"\n'
            '}\n\n'
            "Rules:\n"
            "- category_path must use ' > ' as separator\n"
            "- confidence must be an integer from 0 to 100\n"
            "- Be as specific as possible in the category path"
        )

        response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            config=config,
            log_model=self._name,
            log_res_id=self.id,
        )

        if not response:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Categorization Failed'),
                    'message': _('The AI did not return a response. Check your AI configuration.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        data = self._parse_json_response(response)
        if not data or not isinstance(data, dict):
            _logger.warning(
                "AI categorization: could not parse response for product %s: %s",
                self.id, response[:200],
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Categorization Failed'),
                    'message': _('Could not parse AI response. Try again.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        category_path = data.get('category_path', '')
        confidence = data.get('confidence', 0)
        # Clamp confidence to 0-100
        confidence = max(0, min(100, float(confidence)))

        self.write({
            'ai_category_suggestion': category_path,
            'ai_category_confidence': confidence,
            'ai_category_path': category_path,
            'ai_category_date': fields.Datetime.now(),
        })

        reasoning = data.get('reasoning', '')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Product Categorized'),
                'message': _('Category: %s (%.0f%% confidence)\n%s') % (
                    category_path, confidence, reasoning),
                'type': 'success',
                'sticky': True,
            },
        }

    def action_apply_category(self):
        """Look up a matching category mapping and apply the Odoo category."""
        self.ensure_one()
        if not self.ai_category_suggestion:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No AI Category'),
                    'message': _('Run AI categorization first.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        Mapping = self.env['product.ai.category.mapping']

        # Try exact match first, then partial
        mapping = Mapping.search([
            ('name', '=', self.ai_category_suggestion),
        ], limit=1)

        if not mapping:
            # Try matching the last segment of the path
            parts = self.ai_category_suggestion.split(' > ')
            if parts:
                mapping = Mapping.search([
                    ('name', 'ilike', parts[-1]),
                ], limit=1)

        if not mapping or not mapping.odoo_category_id:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Mapping Found'),
                    'message': _(
                        'No category mapping found for "%s". '
                        'Create one in AI Categorizer > Mappings.'
                    ) % self.ai_category_suggestion,
                    'type': 'warning',
                    'sticky': True,
                },
            }

        self.categ_id = mapping.odoo_category_id.id
        mapping.sudo().write({
            'match_count': mapping.match_count + 1,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Category Applied'),
                'message': _('Product category set to "%s".') % mapping.odoo_category_id.display_name,
                'type': 'success',
                'sticky': False,
            },
        }
