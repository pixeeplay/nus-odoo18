import json
import re
import logging
from odoo import fields, models, _

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # ---- Label fields ----

    energy_label = fields.Selection(
        selection=[
            ('A', 'A'), ('B', 'B'), ('C', 'C'),
            ('D', 'D'), ('E', 'E'), ('F', 'F'), ('G', 'G'),
        ],
        string='Energy Label (EU)',
        help='EU Energy Efficiency Rating (A to G).',
    )

    repairability_index = fields.Float(
        string='Repairability Index',
        digits=(3, 1),
        help='French Repairability Index (0.0 to 10.0).',
    )

    promo_price = fields.Float(
        string='Promotional Price',
        digits='Product Price',
        default=0.0,
        help='Promotional sale price. Set to 0 for no promotion.',
    )

    promo_active = fields.Boolean(
        string='Promotion Active',
        default=False,
        help='Toggle to display promotional pricing on shelf labels.',
    )

    # ---- Bullet point extraction ----

    def _get_label_bullet_points(self, max_bullets=5):
        """Extract clean bullet points for the product label.

        Priority:
        1. chatgpt_content (HTML) - parse <li> or <p> tags
        2. x_tech_specs (JSON) - key: value pairs
        3. description_sale (Text) - split by newlines
        """
        self.ensure_one()
        bullets = []

        # Priority 1: chatgpt_content
        if hasattr(self, 'chatgpt_content') and self.chatgpt_content:
            bullets = self._parse_html_bullets(str(self.chatgpt_content))

        # Priority 2: x_tech_specs (JSON)
        if not bullets and hasattr(self, 'x_tech_specs') and self.x_tech_specs:
            try:
                specs = self.x_tech_specs
                if isinstance(specs, str):
                    specs = json.loads(specs)
                if isinstance(specs, dict):
                    for key, value in specs.items():
                        if value and str(value).strip():
                            bullets.append(f'{key}: {value}')
                elif isinstance(specs, list):
                    bullets = [str(item) for item in specs if item]
            except (json.JSONDecodeError, TypeError):
                pass

        # Priority 3: description_sale
        if not bullets and self.description_sale:
            lines = self.description_sale.strip().split('\n')
            bullets = [
                line.strip().lstrip('- ').lstrip('* ')
                for line in lines
                if line.strip() and len(line.strip()) > 3
            ]

        return bullets[:max_bullets]

    @staticmethod
    def _parse_html_bullets(html_content):
        """Parse HTML content and extract clean text bullet points."""
        if not html_content:
            return []

        bullets = []
        content = str(html_content)

        # Extract <li> content
        li_matches = re.findall(r'<li[^>]*>(.*?)</li>', content, re.DOTALL | re.IGNORECASE)
        if li_matches:
            for match in li_matches:
                clean = re.sub(r'<[^>]+>', '', match).strip()
                if clean and len(clean) > 3:
                    bullets.append(clean)
            return bullets

        # Fallback: <p> tags
        p_matches = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL | re.IGNORECASE)
        if p_matches:
            for match in p_matches:
                clean = re.sub(r'<[^>]+>', '', match).strip()
                if clean and len(clean) > 3:
                    bullets.append(clean)
            return bullets

        # Last resort: strip HTML and split on newlines
        text = re.sub(r'<br\s*/?>', '\n', content)
        text = re.sub(r'<[^>]+>', '', text)
        for line in text.strip().split('\n'):
            clean = line.strip()
            if clean and len(clean) > 3:
                bullets.append(clean)

        return bullets

    # ---- Action ----

    def action_print_label(self):
        """Open the label print wizard for selected products."""
        return {
            'name': _('Print Product Labels'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.label.print.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_ids': self.ids,
                'active_ids': self.ids,
                'active_model': 'product.template',
            },
        }
