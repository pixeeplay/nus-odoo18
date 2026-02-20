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
