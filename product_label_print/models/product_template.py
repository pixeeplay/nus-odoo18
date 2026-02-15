import json
import re
import logging
from markupsafe import Markup
from odoo import api, fields, models, _
from odoo.tools.image import image_data_uri

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

    label_preview = fields.Html(
        string='Label Preview',
        compute='_compute_label_preview',
        sanitize=False,
    )

    label_data_complete = fields.Boolean(
        string='Label Ready',
        compute='_compute_label_data_complete',
    )

    # ---- Computed preview ----

    @api.depends(
        'name', 'image_128', 'image_1920', 'barcode', 'default_code',
        'list_price', 'categ_id', 'description_sale',
        'energy_label', 'repairability_index',
        'promo_price', 'promo_active',
    )
    def _compute_label_preview(self):
        for product in self:
            product.label_preview = product._build_label_preview_html()

    @api.depends('name', 'image_128', 'barcode', 'list_price')
    def _compute_label_data_complete(self):
        for product in self:
            has_image = bool(product.image_128)
            has_barcode = bool(product.barcode)
            has_price = product.list_price > 0
            has_name = bool(product.name)
            product.label_data_complete = all([has_image, has_barcode, has_price, has_name])

    def _build_label_preview_html(self):
        """Build an HTML preview of the label for the product form."""
        self.ensure_one()
        bullets = self._get_label_bullet_points(5)

        # Product image
        img_html = ''
        img_field = self.image_128
        if img_field:
            try:
                uri = image_data_uri(img_field)
                img_html = f'<img src="{uri}" style="max-width:80px;max-height:80px;object-fit:contain;border-radius:6px;"/>'
            except Exception:
                pass
        if not img_html:
            img_html = '<div style="width:80px;height:80px;background:#f5f5f5;border:1px dashed #ddd;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:10px;">No Image</div>'

        # Brand
        brand_html = ''
        if hasattr(self, 'product_brand_id') and self.product_brand_id:
            if self.product_brand_id.logo:
                try:
                    brand_uri = image_data_uri(self.product_brand_id.logo)
                    brand_html = f'<img src="{brand_uri}" style="max-height:20px;max-width:60px;object-fit:contain;"/>'
                except Exception:
                    brand_html = f'<span style="font-weight:600;color:#666;">{self.product_brand_id.name}</span>'
            else:
                brand_html = f'<span style="font-weight:600;color:#666;">{self.product_brand_id.name}</span>'

        # Company logo
        company_html = ''
        company = self.company_id or self.env.company
        if company.logo:
            try:
                company_uri = image_data_uri(company.logo)
                company_html = f'<img src="{company_uri}" style="max-height:22px;max-width:50px;object-fit:contain;"/>'
            except Exception:
                pass

        # Energy badge
        energy_html = ''
        if self.energy_label:
            colors = {'A': '#00a651', 'B': '#50b748', 'C': '#bed630', 'D': '#fff200', 'E': '#fcb813', 'F': '#f15a22', 'G': '#ed1c24'}
            text_color = '#fff' if self.energy_label not in ('C', 'D') else '#333'
            energy_html = f'<span style="background:{colors.get(self.energy_label, "#999")};color:{text_color};padding:2px 8px;border-radius:3px;font-weight:bold;font-size:13px;">{self.energy_label}</span>'

        # Bullets
        bullets_html = ''
        if bullets:
            items = ''.join(f'<li style="font-size:10px;color:#555;padding:1px 0;">{b}</li>' for b in bullets[:5])
            bullets_html = f'<ul style="list-style:none;padding:0;margin:4px 0 0 0;">{items}</ul>'
        else:
            bullets_html = '<div style="color:#bbb;font-size:10px;font-style:italic;margin-top:4px;">No features - use AI Enrich</div>'

        # Price
        price_html = ''
        if self.promo_active and self.promo_price > 0:
            price_html = f'''
                <span style="text-decoration:line-through;opacity:0.6;font-size:12px;">{self.list_price:.2f}</span>
                <span style="font-size:20px;font-weight:800;color:#ff3333;margin-left:4px;">{self.promo_price:.2f} &euro;</span>
            '''
        else:
            price_html = f'<span style="font-size:20px;font-weight:800;color:#fff;">{self.list_price:.2f} &euro;</span>'

        # DEEE
        deee_html = ''
        deee = getattr(self, 'deee_amount', 0) or 0
        if deee > 0:
            deee_html = f'<span style="font-size:8px;opacity:0.8;">Dont eco-part. {deee:.2f}&euro;</span>'

        # Repairability
        repair_html = ''
        if self.repairability_index > 0:
            score = self.repairability_index
            if score >= 8:
                rc = '#00a651'
            elif score >= 6:
                rc = '#8cc63f'
            elif score >= 4:
                rc = '#fff200'
            elif score >= 2:
                rc = '#f7941d'
            else:
                rc = '#ed1c24'
            text_c = '#fff' if score >= 6 or score < 2 else '#333'
            repair_html = f'<span style="background:{rc};color:{text_c};padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">{score:.1f}/10</span>'

        # Barcode preview
        barcode_html = ''
        if self.barcode:
            barcode_html = f'''
                <div style="text-align:center;padding:8px;">
                    <div style="font-size:11px;font-weight:600;color:#444;margin-bottom:6px;">{self.name or ""}</div>
                    <div style="font-size:10px;color:#888;margin-bottom:8px;">Ref: {self.default_code or "N/A"}</div>
                    <img src="/report/barcode/Code128/{self.barcode}?width=280&amp;height=80&amp;humanreadable=1"
                         style="width:180px;height:55px;" alt="Barcode"/>
                    <div style="font-family:monospace;font-size:12px;letter-spacing:1px;color:#444;margin-top:4px;">{self.barcode}</div>
                </div>
            '''
        else:
            barcode_html = '<div style="text-align:center;color:#bbb;padding:15px;font-size:11px;">No barcode</div>'

        # Category
        categ_html = ''
        if self.categ_id:
            categ_html = f'<span style="font-size:8px;color:#888;background:#f0f0f0;padding:1px 6px;border-radius:8px;text-transform:uppercase;">{self.categ_id.name}</span>'

        # Status indicators
        checks = []
        checks.append(('Image', bool(self.image_128)))
        checks.append(('Barcode', bool(self.barcode)))
        checks.append(('Price', self.list_price > 0))
        checks.append(('Features', bool(bullets)))
        has_ai = hasattr(self, 'chatgpt_enriched') and self.chatgpt_enriched
        checks.append(('AI Enriched', has_ai))

        status_items = ''
        for label, ok in checks:
            icon = '&#10004;' if ok else '&#10008;'
            color = '#28a745' if ok else '#dc3545'
            status_items += f'<span style="color:{color};font-size:10px;margin-right:8px;">{icon} {label}</span>'

        html = f'''
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
            <!-- Status bar -->
            <div style="background:#f8f9fa;border:1px solid #e9ecef;border-radius:8px;padding:8px 12px;margin-bottom:12px;">
                {status_items}
            </div>

            <div style="display:flex;gap:16px;">
                <!-- FRONT PREVIEW -->
                <div style="flex:1;border:1px solid #dee2e6;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
                    <div style="background:#0066cc;color:#fff;padding:4px 10px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">
                        Front Side
                    </div>
                    <!-- Header -->
                    <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-bottom:2px solid #0066cc;">
                        <div>{company_html}</div>
                        <div>{brand_html}</div>
                        <div>{energy_html}</div>
                    </div>
                    <!-- Body -->
                    <div style="display:flex;gap:10px;padding:8px 10px;">
                        <div style="flex:0 0 auto;">{img_html}</div>
                        <div style="flex:1;min-width:0;">
                            <div style="font-size:12px;font-weight:700;color:#222;line-height:1.2;margin-bottom:2px;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">{self.name or "Product Name"}</div>
                            {bullets_html}
                        </div>
                    </div>
                    <!-- Meta -->
                    <div style="display:flex;justify-content:space-between;align-items:center;padding:2px 10px;">
                        {categ_html}
                        {repair_html}
                    </div>
                    <!-- Price bar -->
                    <div style="background:linear-gradient(135deg,#0066cc,#004499);padding:8px 12px;display:flex;align-items:center;justify-content:space-between;border-radius:0 0 9px 9px;">
                        <div>{price_html}</div>
                        <div style="color:#fff;">{deee_html}</div>
                    </div>
                </div>

                <!-- BACK PREVIEW -->
                <div style="flex:0 0 200px;border:1px solid #dee2e6;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
                    <div style="background:#555;color:#fff;padding:4px 10px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">
                        Back Side
                    </div>
                    <div style="display:flex;align-items:center;justify-content:center;min-height:180px;">
                        {barcode_html}
                    </div>
                </div>
            </div>
        </div>
        '''
        return Markup(html)

    # ---- Bullet point extraction ----

    def _get_label_bullet_points(self, max_bullets=5):
        """Extract clean bullet points for the product label."""
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

    # ---- Actions ----

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

    def action_enrich_for_label(self):
        """Enrich the product with AI and refresh the form."""
        self.ensure_one()
        if hasattr(self, 'action_enrich_with_chatgpt'):
            self.action_enrich_with_chatgpt()
        return True
