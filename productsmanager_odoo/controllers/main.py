from odoo import http
from odoo.http import request


class ProductsManagerController(http.Controller):

    @http.route('/productsmanager/search', type='json', auth='user')
    def search_products(self, query='', limit=50, **kwargs):
        """JSON endpoint for AJAX product search."""
        config = request.env['pm.config'].get_active_config()
        api = config._get_api_client()

        results = {
            'pm_products': [],
            'odoo_products': [],
        }

        # Search PM API
        try:
            pm_results = api.search_products(query, limit=limit)
            for p in pm_results:
                results['pm_products'].append({
                    'id': p.get('id'),
                    'name': p.get('name'),
                    'ean': p.get('ean') or p.get('barcode'),
                    'brand': p.get('brand'),
                    'best_price': p.get('best_price') or 0,
                })
        except Exception:
            pass

        # Search Odoo
        products = request.env['product.product'].search([
            '|', '|',
            ('name', 'ilike', query),
            ('default_code', 'ilike', query),
            ('barcode', 'ilike', query),
        ], limit=limit)

        for product in products:
            results['odoo_products'].append({
                'id': product.id,
                'name': product.name,
                'barcode': product.barcode,
                'standard_price': product.standard_price,
                'qty_available': product.qty_available,
            })

        return results
