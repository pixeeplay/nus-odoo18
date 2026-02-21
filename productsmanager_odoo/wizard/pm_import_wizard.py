import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..models.pm_api import ProductsManagerAPIError

_logger = logging.getLogger(__name__)


class PmImportWizard(models.TransientModel):
    _name = 'pm.import.wizard'
    _description = 'Products Manager Search & Import'

    config_id = fields.Many2one('pm.config', string='Configuration', required=True)
    search_mode = fields.Selection([
        ('text', 'Text Search'),
        ('supplier', 'By Supplier'),
    ], string='Search Mode', default='text', required=True)
    search_query = fields.Char(string='Search')
    line_ids = fields.One2many('pm.import.wizard.line', 'wizard_id', string='Results')
    result_count = fields.Integer(compute='_compute_counts')
    pm_count = fields.Integer(string='PM Results', compute='_compute_counts')
    odoo_count = fields.Integer(string='Odoo Results', compute='_compute_counts')
    current_page = fields.Integer(string='Page', default=1)
    per_page = fields.Integer(string='Per Page', default=20)
    total_pm = fields.Integer(string='Total PM Results')
    has_next = fields.Boolean(string='Has Next')
    has_previous = fields.Boolean(string='Has Previous')
    page_display = fields.Char(string='Page', compute='_compute_page_display')

    # Supplier search mode fields
    supplier_search_query = fields.Char(string='Filter Suppliers')
    supplier_line_ids = fields.One2many(
        'pm.supplier.wizard.line', 'wizard_id', string='Suppliers',
    )
    pm_supplier_id = fields.Char(string='Selected Supplier ID')
    pm_supplier_name = fields.Char(string='Selected Supplier')
    pm_supplier_code = fields.Char(string='Selected Supplier Code')

    @api.depends('line_ids', 'line_ids.source')
    def _compute_counts(self):
        for wizard in self:
            wizard.result_count = len(wizard.line_ids)
            wizard.pm_count = len(wizard.line_ids.filtered(lambda l: l.source == 'pm'))
            wizard.odoo_count = len(wizard.line_ids.filtered(lambda l: l.source == 'odoo'))

    @api.depends('current_page', 'total_pm', 'per_page')
    def _compute_page_display(self):
        for wizard in self:
            total_pages = max(1, (wizard.total_pm + wizard.per_page - 1) // wizard.per_page) if wizard.per_page else 1
            wizard.page_display = f'Page {wizard.current_page}/{total_pages} ({wizard.total_pm} PM total)'

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'config_id' in fields_list and not res.get('config_id'):
            try:
                config = self.env['pm.config'].get_active_config()
                res['config_id'] = config.id
            except UserError:
                pass
        return res

    # ── Search ──────────────────────────────────────────────────────────

    def action_search(self):
        """Search products in both PM API and Odoo. Resets to page 1."""
        self.ensure_one()
        self.current_page = 1
        if self.search_mode == 'supplier' and self.pm_supplier_id:
            return self._do_supplier_search()
        return self._do_search()

    def action_next_page(self):
        """Go to next page of results."""
        self.ensure_one()
        self.current_page += 1
        if self.search_mode == 'supplier' and self.pm_supplier_id:
            return self._do_supplier_search()
        return self._do_search()

    def action_prev_page(self):
        """Go to previous page of results."""
        self.ensure_one()
        self.current_page = max(1, self.current_page - 1)
        if self.search_mode == 'supplier' and self.pm_supplier_id:
            return self._do_supplier_search()
        return self._do_search()

    def _do_search(self):
        """Execute search: PM API + Odoo, then group matching products."""
        self.ensure_one()
        if not self.search_query:
            raise UserError(_('Please enter a search query.'))

        self.line_ids.unlink()
        pm_lines = []
        odoo_lines = []
        page = self.current_page
        per_page = self.per_page

        # ── 1. Search Products Manager API ──
        api = None
        try:
            api = self.config_id._get_api_client()
            pm_results, meta = self.config_id._api_call(
                'search_products',
                self.search_query, page=page, per_page=per_page,
            )
            _logger.info('PM search p%d: %d results, meta=%s', page, len(pm_results), meta)

            self.total_pm = meta.get('total', len(pm_results))
            self.has_next = meta.get('has_next', len(pm_results) >= per_page)
            self.has_previous = meta.get('has_previous', page > 1)

            for pm_prod in pm_results:
                line_vals = self._pm_product_to_line(pm_prod, api)
                if line_vals:
                    pm_lines.append(line_vals)
        except ProductsManagerAPIError as exc:
            _logger.warning('PM search failed: %s', exc)

        # ── 2. Search Odoo products ──
        try:
            with self.env.cr.savepoint():
                query_param = f'%{self.search_query}%'
                offset = (page - 1) * per_page
                self.env.cr.execute("""
                    SELECT pp.id, pt.name, pp.default_code, pp.barcode,
                           pt.list_price,
                           pp.pm_external_id, pp.pm_brand
                    FROM product_product pp
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE pp.active = true
                      AND (pt.name::text ILIKE %s
                           OR pp.default_code ILIKE %s
                           OR pp.barcode ILIKE %s)
                    LIMIT %s OFFSET %s
                """, (query_param, query_param, query_param, per_page, offset))
                rows = self.env.cr.dictfetchall()
                _logger.info('Odoo search: %d results', len(rows))
                for row in rows:
                    line_vals = self._odoo_row_to_line(row)
                    odoo_lines.append(line_vals)
        except Exception as exc:
            _logger.warning('Odoo product search failed: %s', exc, exc_info=True)

        # ── 3. Smart matching: group PM + Odoo by barcode ──
        lines = self._group_results(pm_lines, odoo_lines)

        self.write({'line_ids': [(0, 0, lv) for lv in lines]})

        self.env['pm.sync.log'].log(
            config_id=self.config_id.id,
            operation='search',
            message=f'Search "{self.search_query}" p{page}: {len(pm_lines)} PM + {len(odoo_lines)} Odoo',
            product_count=len(lines),
        )

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pm.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _group_results(self, pm_lines, odoo_lines):
        """Group PM and Odoo results by barcode so similar products appear together.

        For each PM product, if a matching Odoo product exists (same barcode),
        set match_odoo_id on the PM line and place them consecutively.
        """
        # Build barcode → Odoo line index
        odoo_by_barcode = {}
        for lv in odoo_lines:
            bc = (lv.get('barcode') or '').strip()
            if bc:
                odoo_by_barcode.setdefault(bc, []).append(lv)

        used_odoo_barcodes = set()
        result = []

        for pm_lv in pm_lines:
            pm_bc = (pm_lv.get('barcode') or '').strip()
            # Find matching Odoo product
            if pm_bc and pm_bc in odoo_by_barcode:
                matching_odoo = odoo_by_barcode[pm_bc]
                if matching_odoo:
                    # Mark PM line as having a match
                    odoo_match = matching_odoo[0]
                    pm_lv['match_odoo_id'] = odoo_match.get('odoo_product_id')
                    # If already imported by pm_external_id, mark it
                    if not pm_lv.get('already_imported') and odoo_match.get('odoo_product_id'):
                        pm_lv['already_imported'] = bool(
                            odoo_match.get('barcode') == pm_bc
                            and self.env['product.product'].browse(
                                odoo_match['odoo_product_id']
                            ).pm_external_id
                        )
                    used_odoo_barcodes.add(pm_bc)

            result.append(pm_lv)

            # Place matching Odoo product right after the PM product
            if pm_bc and pm_bc in odoo_by_barcode:
                for odoo_lv in odoo_by_barcode[pm_bc]:
                    result.append(odoo_lv)

        # Add remaining Odoo products that didn't match any PM product
        for lv in odoo_lines:
            bc = (lv.get('barcode') or '').strip()
            if bc not in used_odoo_barcodes:
                result.append(lv)
            elif bc in used_odoo_barcodes:
                pass  # Already added above

        return result

    # ── PM product parsing ───────────────────────────────────────────────

    def _pm_product_to_line(self, pm_prod, api=None):
        """Convert a PM API ProductResponse dict to wizard line values."""
        pm_id = str(pm_prod.get('id') or pm_prod.get('product_id') or '')
        if not pm_id:
            return None

        name = pm_prod.get('name') or 'Unknown'
        ean = pm_prod.get('ean') or pm_prod.get('barcode') or ''
        brand = pm_prod.get('brand') or pm_prod.get('brand_name') or ''

        # Check if already imported (use savepoint to protect transaction)
        already = False
        match_odoo_id = False
        try:
            with self.env.cr.savepoint():
                existing = self.env['product.product'].search([
                    ('pm_external_id', '=', pm_id),
                ], limit=1)
                if existing:
                    already = True
                    match_odoo_id = existing.id
        except Exception:
            pass

        # Extract suppliers from the product's "prices" array
        suppliers = self._extract_pm_suppliers(pm_prod)

        # If no suppliers with prices, try the price-comparison endpoint
        has_prices = any(s.get('price', 0) > 0 for s in suppliers)
        if api and not has_prices:
            try:
                price_data = api.get_price_comparison(pm_id)
                if isinstance(price_data, dict) and price_data.get('prices'):
                    suppliers = self._parse_price_entries(price_data['prices'])
            except ProductsManagerAPIError:
                pass

        # If still no prices, try get_product_suppliers
        has_prices = any(s.get('price', 0) > 0 for s in suppliers)
        if api and not has_prices:
            try:
                sup_data = api.get_product_suppliers(pm_id)
                if isinstance(sup_data, list):
                    enriched = self._parse_price_entries(sup_data)
                    if enriched:
                        suppliers = enriched
            except ProductsManagerAPIError:
                pass

        # Product-level price fields
        best_price = _safe_float(pm_prod.get('best_price') or pm_prod.get('cost_price')
                                 or pm_prod.get('price') or 0)
        best_supplier = pm_prod.get('supplier_name') or ''
        total_stock = _safe_int(pm_prod.get('stock_quantity') or 0)

        # Override with supplier data if available
        for s in suppliers:
            total_stock += s.get('stock', 0)
            price = s.get('price', 0)
            if price > 0 and (best_price == 0 or price < best_price):
                best_price = price
                best_supplier = s['name']

        vals = {
            'source': 'pm',
            'pm_id': pm_id,
            'name': name,
            'brand': brand,
            'barcode': ean,
            'best_price': best_price,
            'best_supplier': best_supplier,
            'total_stock': total_stock,
            'supplier_count': len(suppliers),
            'already_imported': already,
            'match_odoo_id': match_odoo_id,
            'supplier_data_json': json.dumps(suppliers),
        }

        for i, s in enumerate(suppliers[:3], 1):
            vals[f'supplier_{i}_name'] = s.get('name', '')
            vals[f'supplier_{i}_price'] = s.get('price', 0)
            vals[f'supplier_{i}_stock'] = s.get('stock', 0)

        return vals

    def _extract_pm_suppliers(self, pm_prod):
        """Extract supplier info from PM product data."""
        raw = pm_prod.get('prices') or []
        if raw and isinstance(raw, list):
            return self._parse_price_entries(raw)

        supplier_name = pm_prod.get('supplier_name') or ''
        cost_price = _safe_float(pm_prod.get('cost_price') or pm_prod.get('price') or 0)
        stock = _safe_int(pm_prod.get('stock_quantity') or 0)
        if supplier_name and (cost_price > 0 or stock > 0):
            return [{
                'name': supplier_name,
                'price': cost_price,
                'stock': stock,
                'moq': _safe_int(pm_prod.get('moq') or 1),
                'is_best': True,
            }]

        return []

    def _parse_price_entries(self, entries):
        """Parse an array of PriceComparisonEntry objects into supplier dicts."""
        suppliers = []
        if not isinstance(entries, list):
            return suppliers
        for s in entries:
            if not isinstance(s, dict):
                continue
            name = (s.get('supplier_name') or s.get('name')
                    or s.get('supplier') or s.get('supplier_code') or 'Unknown')
            price = _safe_float(
                s.get('current_price') or s.get('cost_price') or s.get('price') or 0
            )
            stock = _safe_int(
                s.get('stock_quantity') or s.get('stock') or s.get('quantity') or 0
            )
            suppliers.append({
                'name': name,
                'price': price,
                'stock': stock,
                'moq': _safe_int(s.get('moq') or s.get('min_order_quantity') or 1),
                'is_best': bool(s.get('is_primary') or s.get('is_best')),
            })
        return suppliers

    # ── Odoo product parsing ─────────────────────────────────────────────

    def _odoo_row_to_line(self, row):
        """Convert a raw SQL row to wizard line values, including Odoo supplier info."""
        name = row.get('name') or ''
        if isinstance(name, dict):
            name = name.get('en_US') or name.get('fr_FR') or next(iter(name.values()), '')

        product_id = row['id']
        list_price = row.get('list_price') or 0
        # list_price can be jsonb in some Odoo 18 setups
        if isinstance(list_price, dict):
            list_price = next((v for v in list_price.values() if v), 0)
        best_price = _safe_float(list_price)
        best_supplier = ''
        total_stock = 0
        suppliers = []

        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    SELECT si.price, si.min_qty, si.delay,
                           rp.name AS partner_name, rp.id AS partner_id
                    FROM product_supplierinfo si
                    JOIN res_partner rp ON rp.id = si.partner_id
                    JOIN product_product pp ON pp.product_tmpl_id = si.product_tmpl_id
                    WHERE pp.id = %s
                    ORDER BY si.price ASC
                    LIMIT 3
                """, (product_id,))
                sup_rows = self.env.cr.dictfetchall()
                for sr in sup_rows:
                    partner_name = sr.get('partner_name') or ''
                    if isinstance(partner_name, dict):
                        partner_name = next(iter(partner_name.values()), '') if partner_name else ''
                    sup_price = sr.get('price') or 0
                    suppliers.append({
                        'name': partner_name,
                        'price': sup_price,
                        'stock': 0,
                        'moq': int(sr.get('min_qty') or 1),
                    })
                    if sup_price > 0 and (not best_supplier or sup_price < best_price):
                        best_price = sup_price
                        best_supplier = partner_name
        except Exception:
            _logger.debug('Odoo supplier info fetch failed for pp.id=%s', product_id, exc_info=True)

        line_vals = {
            'source': 'odoo',
            'odoo_product_id': product_id,
            'name': name,
            'brand': row.get('pm_brand') or '',
            'barcode': row.get('barcode') or '',
            'best_price': best_price,
            'best_supplier': best_supplier,
            'total_stock': total_stock,
            'supplier_count': len(suppliers),
            'already_imported': bool(row.get('pm_external_id')),
            'supplier_data_json': json.dumps(suppliers),
        }

        for i, s in enumerate(suppliers[:3], 1):
            line_vals[f'supplier_{i}_name'] = s.get('name', '')
            line_vals[f'supplier_{i}_price'] = s.get('price', 0)
            line_vals[f'supplier_{i}_stock'] = s.get('stock', 0)

        return line_vals

    # ── Supplier search mode ─────────────────────────────────────────────

    def action_search_suppliers(self):
        """Fetch suppliers from PM API and display in supplier_line_ids."""
        self.ensure_one()
        try:
            suppliers, _meta = self.config_id._api_call(
                'search_suppliers',
                search=self.supplier_search_query or '',
                page=1,
                per_page=50,
            )
        except ProductsManagerAPIError as exc:
            raise UserError(_('Failed to fetch suppliers: %s') % exc)

        self.supplier_line_ids.unlink()
        lines = []
        for s in suppliers:
            lines.append((0, 0, {
                'pm_supplier_id': str(s.get('id') or ''),
                'pm_supplier_code': s.get('code') or s.get('supplier_code') or '',
                'name': s.get('name') or s.get('supplier_name') or 'Unknown',
                'is_active': s.get('is_active', True),
                'product_count': s.get('products_count') or s.get('product_count') or 0,
            }))
        self.write({'supplier_line_ids': lines})
        return self._return_wizard()

    def action_load_supplier_products(self):
        """Load products for the selected supplier."""
        self.ensure_one()
        selected = self.supplier_line_ids.filtered('selected')[:1]
        if not selected:
            raise UserError(_('Please select a supplier.'))
        self.pm_supplier_id = selected.pm_supplier_id
        self.pm_supplier_name = selected.name
        self.pm_supplier_code = selected.pm_supplier_code
        self.current_page = 1
        return self._do_supplier_search()

    def _do_supplier_search(self):
        """Search products for the selected supplier (paginated)."""
        self.ensure_one()
        if not self.pm_supplier_id:
            raise UserError(_('No supplier selected.'))

        self.line_ids.unlink()
        lines = []
        api = self.config_id._get_api_client()

        try:
            pm_results, meta = self.config_id._api_call(
                'get_supplier_products',
                self.pm_supplier_id,
                page=self.current_page,
                per_page=self.per_page,
            )
            self.total_pm = meta.get('total', len(pm_results))
            self.has_next = meta.get('has_next', len(pm_results) >= self.per_page)
            self.has_previous = meta.get('has_previous', self.current_page > 1)

            for pm_prod in pm_results:
                line_vals = self._pm_product_to_line(pm_prod, api)
                if line_vals:
                    lines.append((0, 0, line_vals))
        except ProductsManagerAPIError as exc:
            _logger.warning('Supplier product search failed: %s', exc)

        self.write({'line_ids': lines})
        return self._return_wizard()

    def action_import_all_supplier(self):
        """Import ALL products from the selected supplier (all pages)."""
        self.ensure_one()
        if not self.pm_supplier_id:
            raise UserError(_('No supplier selected.'))

        mappings = self.env['pm.field.mapping'].search([
            ('config_id', '=', self.config_id.id),
            ('is_active', '=', True),
        ])

        imported = 0
        errors = 0
        page = 1
        has_next = True

        while has_next:
            try:
                pm_results, meta = self.config_id._api_call(
                    'get_supplier_products',
                    self.pm_supplier_id, page=page, per_page=50,
                )
            except ProductsManagerAPIError:
                break

            for pm_prod in pm_results:
                pm_id = str(pm_prod.get('id') or '')
                if not pm_id:
                    continue
                try:
                    with self.env.cr.savepoint():
                        if self.env['product.product'].search(
                            [('pm_external_id', '=', pm_id)], limit=1,
                        ):
                            continue
                except Exception:
                    pass

                try:
                    line_vals = self._pm_product_to_line(pm_prod, api)
                    if line_vals:
                        line = self.env['pm.import.wizard.line'].create({
                            'wizard_id': self.id,
                            **line_vals,
                        })
                        self._import_single_product(line, mappings)
                        imported += 1
                except Exception as exc:
                    _logger.error(
                        'Supplier import error for PM %s: %s', pm_id, exc,
                    )
                    errors += 1

            has_next = meta.get('has_next', len(pm_results) >= 50)
            page += 1

        self.env['pm.sync.log'].log(
            config_id=self.config_id.id,
            operation='import',
            message=(
                f'Supplier "{self.pm_supplier_name}" full import: '
                f'{imported} imported, {errors} errors'
            ),
            product_count=imported,
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Supplier Import Complete'),
                'message': _('%d products imported, %d errors') % (imported, errors),
                'type': 'success' if not errors else 'warning',
                'sticky': bool(errors),
            },
        }

    def _return_wizard(self):
        """Return action to refresh the wizard form."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pm.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ── Import ──────────────────────────────────────────────────────────

    def action_import_selected(self):
        """Import selected PM products into Odoo."""
        self.ensure_one()
        selected = self.line_ids.filtered(
            lambda l: l.selected and l.source == 'pm' and not l.already_imported
        )
        if not selected:
            raise UserError(_('No products selected for import. '
                              'Select PM products using the checkbox.'))

        imported = 0
        errors = []
        mappings = self.env['pm.field.mapping'].search([
            ('config_id', '=', self.config_id.id),
            ('is_active', '=', True),
        ])

        for line in selected:
            try:
                self._import_single_product(line, mappings)
                line.write({'already_imported': True, 'selected': False})
                imported += 1
            except Exception as exc:
                errors.append(f'{line.name}: {exc}')
                _logger.error('Import failed for %s: %s', line.name, exc)

        self.env['pm.sync.log'].log(
            config_id=self.config_id.id,
            operation='import',
            message=f'Imported {imported} products' + (f', {len(errors)} errors' if errors else ''),
            product_count=imported,
        )

        if errors:
            msg = _('%d products imported, %d errors:\n%s') % (imported, len(errors), '\n'.join(errors))
        else:
            msg = _('%d products imported successfully.') % imported

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Complete'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': bool(errors),
            },
        }

    def _import_single_product(self, line, mappings):
        """Import a single PM product into Odoo."""
        api = self.config_id._get_api_client()
        try:
            pm_data = api.get_product(line.pm_id)
        except ProductsManagerAPIError:
            pm_data = {}

        vals = {
            'name': line.name,
            'barcode': line.barcode or False,
            'standard_price': line.best_price,
            'pm_external_id': line.pm_id,
            'pm_brand': line.brand or False,
            'pm_last_sync': fields.Datetime.now(),
        }

        if mappings and pm_data:
            mapped_vals = mappings.apply_mapping(pm_data)
            vals.update(mapped_vals)

        vals['pm_external_id'] = line.pm_id
        vals['pm_last_sync'] = fields.Datetime.now()

        product = self.env['product.product'].create(vals)

        # Download images from PM data
        if pm_data:
            product._download_pm_images(pm_data)

        suppliers_data = []
        if line.supplier_data_json:
            try:
                suppliers_data = json.loads(line.supplier_data_json)
            except (json.JSONDecodeError, TypeError):
                pass

        if not suppliers_data:
            suppliers_data = self._rebuild_suppliers_from_line(line)

        for s_data in suppliers_data:
            partner = self.config_id._get_or_create_supplier_partner(s_data.get('name', 'Unknown'))
            self.env['product.supplierinfo'].create({
                'product_tmpl_id': product.product_tmpl_id.id,
                'partner_id': partner.id,
                'price': s_data.get('price', 0),
                'min_qty': s_data.get('moq', 1),
                'delay': 1,
                'pm_supplier_stock': s_data.get('stock', 0),
                'pm_is_best': s_data.get('is_best', False),
            })

        return product

    def _rebuild_suppliers_from_line(self, line):
        """Rebuild supplier data from the wizard line columns."""
        suppliers = []
        for i in range(1, 4):
            name = getattr(line, f'supplier_{i}_name', '')
            price = getattr(line, f'supplier_{i}_price', 0)
            if name:
                suppliers.append({
                    'name': name,
                    'price': price,
                    'stock': getattr(line, f'supplier_{i}_stock', 0),
                    'moq': 1,
                })
        return suppliers

    # ── Merge ────────────────────────────────────────────────────────────

    def action_merge_selected(self):
        """Merge selected PM products with their matching Odoo products.

        Only updates prices, stock, and supplier info. Sets pm_external_id
        so the cron auto-updates them going forward.
        """
        self.ensure_one()
        selected = self.line_ids.filtered(
            lambda l: l.selected and l.source == 'pm' and l.match_odoo_id
        )
        if not selected:
            raise UserError(_(
                'No PM products selected that match an existing Odoo product. '
                'Select PM products that have a matching Odoo product (same barcode).'
            ))

        merged = 0
        errors = []
        config = self.config_id

        for line in selected:
            try:
                product = self.env['product.product'].browse(line.match_odoo_id.id)
                if not product.exists():
                    errors.append(f'{line.name}: Odoo product not found')
                    continue

                # Link to PM for auto-update
                product.write({
                    'pm_external_id': line.pm_id,
                    'pm_brand': line.brand or product.pm_brand,
                    'pm_last_sync': fields.Datetime.now(),
                })

                # Update supplier info (prices + stock)
                suppliers_data = []
                if line.supplier_data_json:
                    try:
                        suppliers_data = json.loads(line.supplier_data_json)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if not suppliers_data:
                    suppliers_data = self._rebuild_suppliers_from_line(line)

                config._update_supplier_info(product, suppliers_data)

                # Update standard_price with best price
                if line.best_price > 0:
                    product.standard_price = line.best_price

                line.write({'already_imported': True, 'selected': False})
                merged += 1
            except Exception as exc:
                errors.append(f'{line.name}: {exc}')
                _logger.error('Merge failed for %s: %s', line.name, exc)

        self.env['pm.sync.log'].log(
            config_id=config.id,
            operation='sync',
            message=(
                f'Merged {merged} PM products with Odoo'
                + (f', {len(errors)} errors' if errors else '')
            ),
            product_count=merged,
        )

        if errors:
            msg = _('%d products merged, %d errors:\n%s') % (merged, len(errors), '\n'.join(errors))
        else:
            msg = _(
                '%d products merged successfully.\n'
                'Updated: prices, stock, supplier info.\n'
                'These products will now auto-update via the cron.'
            ) % merged

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Merge Complete'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    def action_merge_overwrite(self):
        """Merge with full overwrite — update ALL fields from PM.

        Updates: prices, stock, supplier info, description, images, brand,
        dimensions, compliance, etc. Shows what was updated.
        """
        self.ensure_one()
        selected = self.line_ids.filtered(
            lambda l: l.selected and l.source == 'pm' and l.match_odoo_id
        )
        if not selected:
            raise UserError(_(
                'No PM products selected that match an existing Odoo product.'
            ))

        merged = 0
        errors = []
        config = self.config_id
        api = config._get_api_client()
        updated_fields_summary = []

        for line in selected:
            try:
                product = self.env['product.product'].browse(line.match_odoo_id.id)
                if not product.exists():
                    errors.append(f'{line.name}: Odoo product not found')
                    continue

                # Link to PM
                product.write({
                    'pm_external_id': line.pm_id,
                    'pm_last_sync': fields.Datetime.now(),
                })

                # Fetch full data from PM
                try:
                    pm_data = api.get_product(line.pm_id)
                except ProductsManagerAPIError:
                    pm_data = {}

                if pm_data:
                    # Update all PM fields
                    full_vals = product._map_full_pm_data(pm_data)
                    full_vals['pm_last_full_fetch'] = fields.Datetime.now()

                    # Track which fields changed
                    changed = []
                    for key, new_val in full_vals.items():
                        if new_val and new_val != getattr(product, key, None):
                            changed.append(key)
                    if changed:
                        updated_fields_summary.append(
                            f'{line.name}: {", ".join(changed[:10])}'
                            + (f' +{len(changed)-10} more' if len(changed) > 10 else '')
                        )

                    product.write(full_vals)

                    # Update prices & stock
                    config._update_product_from_pm(product, pm_data, api)

                    # Download images
                    product._download_pm_images(pm_data)

                    # Try enrichment
                    try:
                        enrichment = api.get_enrichment(line.pm_id)
                        if isinstance(enrichment, dict):
                            product.write(product._map_enrichment_data(enrichment))
                    except ProductsManagerAPIError:
                        pass

                line.write({'already_imported': True, 'selected': False})
                merged += 1
            except Exception as exc:
                errors.append(f'{line.name}: {exc}')
                _logger.error('Merge overwrite failed for %s: %s', line.name, exc)

        self.env['pm.sync.log'].log(
            config_id=config.id,
            operation='sync',
            message=(
                f'Full merge (overwrite) {merged} products'
                + (f', {len(errors)} errors' if errors else '')
            ),
            product_count=merged,
        )

        details = '\n'.join(updated_fields_summary[:5])
        if len(updated_fields_summary) > 5:
            details += f'\n... and {len(updated_fields_summary) - 5} more'

        if errors:
            msg = _('%d products merged (full overwrite), %d errors:\n%s') % (
                merged, len(errors), '\n'.join(errors),
            )
        else:
            msg = _(
                '%d products merged with full overwrite.\n'
                'Updated: prices, stock, suppliers, description, images, '
                'compliance, dimensions, AI enrichment, categories.\n\n%s'
            ) % (merged, details)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Full Merge Complete'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            },
        }

    # ── Navigation ───────────────────────────────────────────────────────

    def action_open_product(self):
        """Open the product form for the selected line."""
        self.ensure_one()
        line = self.line_ids.filtered('selected')[:1]
        if not line:
            raise UserError(_('Please select a product first.'))

        if line.source == 'odoo' and line.odoo_product_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'product.product',
                'res_id': line.odoo_product_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        elif line.source == 'pm' and line.pm_id:
            product = self.env['product.product'].search([
                ('pm_external_id', '=', line.pm_id),
            ], limit=1)
            if product:
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': 'product.product',
                    'res_id': product.id,
                    'view_mode': 'form',
                    'target': 'current',
                }
            raise UserError(_('This product has not been imported yet. Import it first.'))

        raise UserError(_('No product to open.'))

    # ── Bulk actions ────────────────────────────────────────────────────

    def action_select_all(self):
        """Select all PM lines that are not yet imported."""
        self.ensure_one()
        pm_lines = self.line_ids.filtered(
            lambda l: l.source == 'pm' and not l.already_imported
        )
        pm_lines.write({'selected': True})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pm.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_deselect_all(self):
        """Deselect all lines."""
        self.ensure_one()
        self.line_ids.write({'selected': False})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pm.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }


class PmImportWizardLine(models.TransientModel):
    _name = 'pm.import.wizard.line'
    _description = 'Products Manager Import Line'
    _order = 'source desc, best_price asc'

    wizard_id = fields.Many2one('pm.import.wizard', ondelete='cascade')
    selected = fields.Boolean(string=' ')
    source = fields.Selection([
        ('pm', 'Products Manager'),
        ('odoo', 'Odoo'),
    ], required=True)
    pm_id = fields.Char(string='PM ID')
    odoo_product_id = fields.Many2one('product.product', string='Odoo Product')
    match_odoo_id = fields.Many2one(
        'product.product', string='Matching Odoo Product',
        help='Existing Odoo product with the same barcode (for merge)',
    )
    name = fields.Char(string='Name')
    brand = fields.Char(string='Brand')
    barcode = fields.Char(string='EAN / Barcode')
    best_price = fields.Float(string='Best Price', digits='Product Price')
    best_supplier = fields.Char(string='Best Supplier')

    # Supplier 1
    supplier_1_name = fields.Char(string='Supplier 1')
    supplier_1_price = fields.Float(string='Price 1', digits='Product Price')
    supplier_1_stock = fields.Integer(string='Stock 1')

    # Supplier 2
    supplier_2_name = fields.Char(string='Supplier 2')
    supplier_2_price = fields.Float(string='Price 2', digits='Product Price')
    supplier_2_stock = fields.Integer(string='Stock 2')

    # Supplier 3
    supplier_3_name = fields.Char(string='Supplier 3')
    supplier_3_price = fields.Float(string='Price 3', digits='Product Price')
    supplier_3_stock = fields.Integer(string='Stock 3')

    total_stock = fields.Integer(string='Total Stock')
    supplier_count = fields.Integer(string='# Suppliers')
    already_imported = fields.Boolean(string='Imported')
    supplier_data_json = fields.Text(string='Supplier Data JSON')

    def action_open_line_product(self):
        """Open the Odoo product form for this line."""
        self.ensure_one()
        if self.source == 'odoo' and self.odoo_product_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'product.product',
                'res_id': self.odoo_product_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        if self.source == 'pm' and self.pm_id:
            product = self.env['product.product'].search([
                ('pm_external_id', '=', self.pm_id),
            ], limit=1)
            if product:
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': 'product.product',
                    'res_id': product.id,
                    'view_mode': 'form',
                    'target': 'current',
                }
        return False


class PmSupplierWizardLine(models.TransientModel):
    _name = 'pm.supplier.wizard.line'
    _description = 'PM Supplier Selection Line'

    wizard_id = fields.Many2one('pm.import.wizard', ondelete='cascade')
    pm_supplier_id = fields.Char(string='Supplier ID')
    pm_supplier_code = fields.Char(string='Code')
    name = fields.Char(string='Supplier Name')
    is_active = fields.Boolean(string='Active')
    product_count = fields.Integer(string='# Products')
    selected = fields.Boolean(string=' ')


def _safe_float(val):
    """Convert a value to float safely (handles strings like '40.25')."""
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val):
    """Convert a value to int safely."""
    try:
        return int(float(val)) if val else 0
    except (ValueError, TypeError):
        return 0
