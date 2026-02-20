from odoo import http
from odoo.http import request
from odoo.osv import expression


class ThemeNovaController(http.Controller):

    # ── Quick View ────────────────────────────────────────────────────────
    @http.route('/theme_nova/quick_view', type='json', auth='public', website=True)
    def quick_view(self, product_id, **kwargs):
        """Return rendered HTML for product quick view modal."""
        domain = expression.AND([
            request.website.sale_product_domain(),
            [('id', '=', int(product_id))],
        ])
        product = request.env['product.template'].search(domain, limit=1)
        if not product:
            return False

        values = self._prepare_quick_view_values(product)
        return request.env['ir.ui.view']._render_template(
            'theme_nova.quick_view_content', values=values,
        )

    def _prepare_quick_view_values(self, product):
        """Prepare values for the quick view template."""
        combination_info = product._get_combination_info(
            only_template=True, add_qty=1,
        )
        return {
            'product': product,
            'combination_info': combination_info,
            'website': request.website,
            'currency': request.website.currency_id,
        }

    # ── Product Navigation ────────────────────────────────────────────────
    @http.route('/theme_nova/product_nav', type='json', auth='public', website=True)
    def product_nav(self, product_id, **kwargs):
        """Return prev/next product IDs for navigation."""
        domain = request.website.sale_product_domain()
        Product = request.env['product.template']
        products = Product.search(domain, order='website_sequence, id')
        product_ids = products.ids

        current_idx = product_ids.index(int(product_id)) if int(product_id) in product_ids else -1
        if current_idx == -1:
            return {'prev': False, 'next': False}

        prev_id = product_ids[current_idx - 1] if current_idx > 0 else False
        next_id = product_ids[current_idx + 1] if current_idx < len(product_ids) - 1 else False

        result = {'prev': False, 'next': False}
        if prev_id:
            p = Product.browse(prev_id)
            result['prev'] = {
                'id': p.id, 'name': p.name,
                'url': p.website_url,
                'image_url': f'/web/image/product.template/{p.id}/image_128',
            }
        if next_id:
            p = Product.browse(next_id)
            result['next'] = {
                'id': p.id, 'name': p.name,
                'url': p.website_url,
                'image_url': f'/web/image/product.template/{p.id}/image_128',
            }
        return result

    # ── Dynamic Products Snippet ──────────────────────────────────────────
    @http.route('/theme_nova/get_products', type='json', auth='public', website=True)
    def get_products(self, category_id=None, limit=8, order='website_sequence', **kwargs):
        """Return product data for dynamic snippet rendering."""
        domain = request.website.sale_product_domain()
        if category_id:
            domain = expression.AND([domain, [('public_categ_ids', 'child_of', int(category_id))]])

        Product = request.env['product.template']
        products = Product.search(domain, limit=int(limit), order=order)

        currency = request.website.currency_id
        result = []
        for product in products:
            combination_info = product._get_combination_info(only_template=True, add_qty=1)
            result.append({
                'id': product.id,
                'name': product.name,
                'url': product.website_url,
                'image_url': f'/web/image/product.template/{product.id}/image_256',
                'price': combination_info.get('price', 0),
                'list_price': combination_info.get('list_price', 0),
                'has_discount': combination_info.get('has_discounted_price', False),
                'currency_symbol': currency.symbol,
                'currency_position': currency.position,
                'label': product.nova_label_id.name if product.nova_label_id else False,
                'label_bg': product.nova_label_id.background_color if product.nova_label_id else False,
                'label_color': product.nova_label_id.text_color if product.nova_label_id else False,
            })
        return result

    # ── Search Autocomplete ───────────────────────────────────────────────
    @http.route('/theme_nova/search_autocomplete', type='json', auth='public', website=True)
    def search_autocomplete(self, query='', limit=6, **kwargs):
        """Return products and categories matching the search query."""
        query = query.strip()
        if len(query) < 2:
            return {'products': [], 'categories': []}

        # Search products
        domain = expression.AND([
            request.website.sale_product_domain(),
            [('name', 'ilike', query)],
        ])
        Product = request.env['product.template']
        products = Product.search(domain, limit=int(limit), order='website_sequence')

        currency = request.website.currency_id
        product_list = []
        for p in products:
            combination_info = p._get_combination_info(only_template=True, add_qty=1)
            price = combination_info.get('price', 0)
            if currency.position == 'before':
                price_str = f'{currency.symbol}\N{NO-BREAK SPACE}{price:.2f}'
            else:
                price_str = f'{price:.2f}\N{NO-BREAK SPACE}{currency.symbol}'
            product_list.append({
                'id': p.id,
                'name': p.name,
                'url': p.website_url,
                'image_url': f'/web/image/product.template/{p.id}/image_128',
                'price_formatted': price_str,
            })

        # Search categories
        Category = request.env['product.public.category']
        categories = Category.search([('name', 'ilike', query)], limit=4)
        category_list = [{'id': c.id, 'name': c.name} for c in categories]

        return {'products': product_list, 'categories': category_list}

    # ── Product Swatches ──────────────────────────────────────────────────
    @http.route('/theme_nova/get_swatches', type='json', auth='public', website=True)
    def get_swatches(self, product_id, **kwargs):
        """Return variant swatches (color/size) for a product."""
        domain = expression.AND([
            request.website.sale_product_domain(),
            [('id', '=', int(product_id))],
        ])
        product = request.env['product.template'].search(domain, limit=1)
        if not product or product.product_variant_count <= 1:
            return []

        # Find the "Color" attribute (or first attribute with type=color)
        color_attr = None
        for line in product.attribute_line_ids:
            if line.attribute_id.display_type == 'color':
                color_attr = line
                break
        # Fallback: use first attribute with <= 10 values
        if not color_attr:
            for line in product.attribute_line_ids:
                if len(line.value_ids) <= 10:
                    color_attr = line
                    break

        if not color_attr:
            return []

        swatches = []
        for value in color_attr.value_ids:
            swatch = {
                'name': value.name,
                'html_color': value.html_color or False,
                'image_url': False,
                'product_image_url': False,
            }
            if value.image:
                swatch['image_url'] = f'/web/image/product.attribute.value/{value.id}/image/30x30'

            # Find variant with this attribute value to get its image
            variant = product.product_variant_ids.filtered(
                lambda v: value in v.product_template_attribute_value_ids.mapped('product_attribute_value_id')
            )[:1]
            if variant and variant.image_variant_1920:
                swatch['product_image_url'] = f'/web/image/product.product/{variant.id}/image_256'
            else:
                swatch['product_image_url'] = f'/web/image/product.template/{product.id}/image_256'

            swatches.append(swatch)

        return swatches
