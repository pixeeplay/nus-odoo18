# -*- coding: utf-8 -*-
import logging
import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductRagIndex(models.Model):
    _name = 'product.rag.index'
    _description = 'Product RAG Index'
    _inherit = ['ollama.mixin']
    _rec_name = 'product_id'

    product_id = fields.Many2one(
        'product.template',
        string='Product',
        required=True,
        ondelete='cascade',
        index=True,
    )
    indexed_text = fields.Text(
        string='Indexed Text',
        help='Concatenated product data used for search: name, description, price, category, etc.',
    )
    last_indexed = fields.Datetime(
        string='Last Indexed',
        readonly=True,
    )

    _sql_constraints = [
        ('product_unique', 'unique(product_id)',
         'Each product can only have one index record!'),
    ]

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    def action_index_product(self):
        """Build the indexed_text from the linked product's data."""
        for rec in self:
            product = rec.product_id
            if not product:
                continue

            parts = []

            # Name
            if product.name:
                parts.append(f"Product: {product.name}")

            # Category
            if product.categ_id:
                parts.append(f"Category: {product.categ_id.complete_name or product.categ_id.name}")

            # Price
            if product.list_price:
                parts.append(f"Price: {product.list_price:.2f}")

            # Sale description
            if product.description_sale:
                parts.append(f"Description: {product.description_sale}")

            # Internal description
            if product.description:
                desc = product.description
                if desc != product.description_sale:
                    parts.append(f"Details: {desc}")

            # Website description (strip HTML)
            website_desc = getattr(product, 'website_description', None)
            if website_desc:
                clean = re.sub(r'<[^>]+>', ' ', str(website_desc))
                clean = re.sub(r'\s+', ' ', clean).strip()
                if clean and clean != (product.description_sale or ''):
                    parts.append(f"Web description: {clean[:1000]}")

            # Default code / barcode
            if product.default_code:
                parts.append(f"Reference: {product.default_code}")
            if getattr(product, 'barcode', None):
                parts.append(f"Barcode: {product.barcode}")

            # Attribute values (e.g. Color: Red, Size: L)
            if product.attribute_line_ids:
                for line in product.attribute_line_ids:
                    attr_name = line.attribute_id.name
                    values = ', '.join(line.value_ids.mapped('name'))
                    if values:
                        parts.append(f"{attr_name}: {values}")

            # Tags / labels
            if hasattr(product, 'product_tag_ids') and product.product_tag_ids:
                tags = ', '.join(product.product_tag_ids.mapped('name'))
                parts.append(f"Tags: {tags}")

            indexed_text = '\n'.join(parts)

            rec.write({
                'indexed_text': indexed_text,
                'last_indexed': fields.Datetime.now(),
            })

        return True

    # ------------------------------------------------------------------
    # Catalog reindex (used by cron)
    # ------------------------------------------------------------------
    @api.model
    def reindex_catalog(self):
        """Create or update index records for all published products.

        Called by the scheduled action (cron).
        """
        _logger.info("RAG: Starting catalog reindex...")

        # Try published products first (website_sale), fall back to all
        Product = self.env['product.template']
        try:
            products = Product.search([
                ('sale_ok', '=', True),
                ('website_published', '=', True),
            ])
        except Exception:
            # website_published may not exist if website_sale is not fully loaded
            products = Product.search([('sale_ok', '=', True)])

        if not products:
            products = Product.search([])

        _logger.info("RAG: Found %d products to index.", len(products))

        existing = self.search([])
        existing_map = {rec.product_id.id: rec for rec in existing}

        created = 0
        updated = 0

        for product in products:
            if product.id in existing_map:
                idx = existing_map[product.id]
                idx.action_index_product()
                updated += 1
            else:
                idx = self.create({'product_id': product.id})
                idx.action_index_product()
                created += 1

        _logger.info("RAG: Reindex done. Created: %d, Updated: %d", created, updated)
        return True

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    @api.model
    def search_products(self, query, limit=5):
        """Search the product index using SQL ILIKE.

        Splits the query into keywords and matches against indexed_text.
        Returns a list of dicts: [{'product_id': int, 'product_name': str, 'snippet': str}]
        """
        if not query or not query.strip():
            return []

        # Split query into meaningful keywords (3+ chars)
        keywords = [
            kw.strip().lower()
            for kw in re.split(r'[\s,;.!?]+', query)
            if len(kw.strip()) >= 3
        ]
        if not keywords:
            keywords = [query.strip().lower()]

        # Build WHERE clause: all keywords must appear (AND logic)
        conditions = []
        params = []
        for kw in keywords:
            conditions.append("LOWER(idx.indexed_text) LIKE %s")
            params.append(f"%{kw}%")

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT
                idx.id AS index_id,
                idx.product_id,
                pt.name->>'en_US' AS product_name,
                COALESCE(pt.name->>'en_US', '') AS fallback_name,
                LEFT(idx.indexed_text, 300) AS snippet
            FROM product_rag_index idx
            JOIN product_template pt ON pt.id = idx.product_id
            WHERE idx.indexed_text IS NOT NULL
              AND {where_clause}
            ORDER BY idx.last_indexed DESC NULLS LAST
            LIMIT %s
        """
        params.append(limit)

        self.env.cr.execute(sql, tuple(params))
        rows = self.env.cr.dictfetchall()

        results = []
        for row in rows:
            name = row.get('product_name') or row.get('fallback_name') or ''
            # If JSONB returns None, get name from ORM
            if not name:
                product = self.env['product.template'].browse(row['product_id'])
                name = product.name or ''
            results.append({
                'product_id': row['product_id'],
                'product_name': name,
                'snippet': (row.get('snippet') or '')[:300],
            })

        return results
