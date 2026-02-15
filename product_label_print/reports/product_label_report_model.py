import base64
import logging
from odoo import api, models
from odoo.tools.image import image_data_uri

_logger = logging.getLogger(__name__)


class ReportProductLabels(models.AbstractModel):
    _name = 'report.product_label_print.report_product_labels'
    _description = 'Product Labels Report'

    def _generate_barcode_uri(self, barcode_value):
        """Generate barcode as base64 data URI using Odoo's built-in method."""
        if not barcode_value:
            return None
        try:
            barcode_bytes = self.env['ir.actions.report'].barcode(
                'Code128', barcode_value,
                width=600, height=150, humanreadable=1,
            )
            b64 = base64.b64encode(barcode_bytes).decode('ascii')
            return f'data:image/png;base64,{b64}'
        except Exception as e:
            _logger.warning('Barcode generation failed for %s: %s', barcode_value, e)
            return None

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        product_ids = data.get('product_ids', docids or [])
        quantity = data.get('quantity', 1)
        show_promo = data.get('show_promo', True)
        show_energy_label = data.get('show_energy_label', True)
        show_repairability = data.get('show_repairability', True)
        show_deee = data.get('show_deee', True)
        max_bullets = data.get('max_bullets', 5)

        products = self.env['product.template'].browse(product_ids).exists()

        # Build expanded list: each product repeated `quantity` times
        expanded = []
        for product in products:
            bullets = product._get_label_bullet_points(max_bullets)

            # Company logo
            company = product.company_id or self.env.company
            company_logo = None
            if company.logo:
                try:
                    company_logo = image_data_uri(company.logo)
                except Exception:
                    pass

            # Product image (use image_1920 for better quality)
            product_image = None
            img_field = product.image_1920 or product.image_128
            if img_field:
                try:
                    product_image = image_data_uri(img_field)
                except Exception:
                    pass

            # Brand logo
            brand_logo = None
            brand_name = ''
            if hasattr(product, 'product_brand_id') and product.product_brand_id:
                brand_name = product.product_brand_id.name or ''
                if product.product_brand_id.logo:
                    try:
                        brand_logo = image_data_uri(product.product_brand_id.logo)
                    except Exception:
                        pass

            # DEEE amount
            deee = 0.0
            if hasattr(product, 'deee_amount') and product.deee_amount:
                deee = product.deee_amount

            # Barcode as base64
            barcode_uri = self._generate_barcode_uri(product.barcode)

            item = {
                'product': product,
                'bullets': bullets,
                'company_logo': company_logo,
                'product_image': product_image,
                'brand_logo': brand_logo,
                'brand_name': brand_name,
                'deee_amount': deee,
                'barcode': product.barcode or '',
                'barcode_uri': barcode_uri,
                'default_code': product.default_code or '',
                'name': product.name or '',
                'list_price': product.list_price,
                'promo_price': product.promo_price if hasattr(product, 'promo_price') else 0.0,
                'promo_active': product.promo_active if hasattr(product, 'promo_active') else False,
                'energy_label': product.energy_label if hasattr(product, 'energy_label') else False,
                'repairability_index': product.repairability_index if hasattr(product, 'repairability_index') else 0.0,
                'categ_name': product.categ_id.name if product.categ_id else '',
            }

            for _i in range(quantity):
                expanded.append(item)

        # Group into pages of 2 labels each (2 rows per A4)
        pages = []
        for i in range(0, len(expanded), 2):
            pages.append(expanded[i:i + 2])

        return {
            'doc_ids': product_ids,
            'doc_model': 'product.template',
            'docs': products,
            'pages': pages,
            'show_promo': show_promo,
            'show_energy_label': show_energy_label,
            'show_repairability': show_repairability,
            'show_deee': show_deee,
        }
