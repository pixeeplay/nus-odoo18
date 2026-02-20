from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    pm_external_id = fields.Char(string='PM External ID', index=True, copy=False)
    pm_last_sync = fields.Datetime(string='PM Last Sync', readonly=True, copy=False)
    pm_brand = fields.Char(string='PM Brand')
    pm_completeness = fields.Integer(string='PM Completeness (%)', default=0)
    pm_supplier_prices_html = fields.Html(
        string='Supplier Prices',
        compute='_compute_pm_supplier_prices_html',
        sanitize=False,
    )

    @api.depends('seller_ids', 'seller_ids.price', 'seller_ids.partner_id')
    def _compute_pm_supplier_prices_html(self):
        for product in self:
            sellers = product.seller_ids
            if not sellers:
                product.pm_supplier_prices_html = '<em>No supplier info</em>'
                continue
            rows = []
            for s in sellers:
                rows.append(
                    f'<tr><td>{s.partner_id.name}</td>'
                    f'<td style="text-align:right">{s.price:.2f} {s.currency_id.symbol}</td>'
                    f'<td style="text-align:right">{int(s.min_qty)}</td></tr>'
                )
            html = (
                '<table class="table table-sm table-striped mb-0">'
                '<thead><tr><th>Supplier</th><th>Price</th><th>MOQ</th></tr></thead>'
                '<tbody>' + ''.join(rows) + '</tbody></table>'
            )
            product.pm_supplier_prices_html = html
