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
    search_query = fields.Char(string='Search')
    line_ids = fields.One2many('pm.import.wizard.line', 'wizard_id', string='Results')
    result_count = fields.Integer(compute='_compute_counts')
    pm_count = fields.Integer(string='PM Results', compute='_compute_counts')
    odoo_count = fields.Integer(string='Odoo Results', compute='_compute_counts')

    @api.depends('line_ids', 'line_ids.source')
    def _compute_counts(self):
        for wizard in self:
            wizard.result_count = len(wizard.line_ids)
            wizard.pm_count = len(wizard.line_ids.filtered(lambda l: l.source == 'pm'))
            wizard.odoo_count = len(wizard.line_ids.filtered(lambda l: l.source == 'odoo'))

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
        """Search products in both PM API and Odoo."""
        self.ensure_one()
        if not self.search_query:
            raise UserError(_('Please enter a search query.'))

        self.line_ids.unlink()
        lines = []

        # Search Products Manager API
        try:
            api = self.config_id._get_api_client()
            pm_results = api.search_products(self.search_query, limit=50)
            for pm_prod in pm_results:
                line_vals = self._pm_product_to_line(pm_prod)
                if line_vals:
                    lines.append((0, 0, line_vals))
        except ProductsManagerAPIError as exc:
            _logger.warning('PM search failed: %s', exc)

        # Search Odoo products (use savepoint to isolate SQL failures
        # from broken columns in other modules like theme_nova)
        try:
            with self.env.cr.savepoint():
                odoo_products = self.env['product.product'].sudo().search([
                    '|', '|',
                    ('name', 'ilike', self.search_query),
                    ('default_code', 'ilike', self.search_query),
                    ('barcode', 'ilike', self.search_query),
                ], limit=50)
                # Read minimal fields only to avoid triggering broken columns
                odoo_data = odoo_products.read([
                    'id', 'name', 'default_code', 'barcode', 'standard_price',
                    'pm_external_id', 'pm_brand',
                ])
                for data in odoo_data:
                    line_vals = self._odoo_data_to_line(data)
                    lines.append((0, 0, line_vals))
        except Exception as exc:
            _logger.warning('Odoo product search failed: %s', exc)

        self.write({'line_ids': lines})

        # Log the search
        self.env['pm.sync.log'].log(
            config_id=self.config_id.id,
            operation='search',
            message=f'Search "{self.search_query}": {len(lines)} results',
            product_count=len(lines),
        )

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pm.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _pm_product_to_line(self, pm_prod):
        """Convert a PM API product dict to wizard line values."""
        pm_id = str(pm_prod.get('id') or pm_prod.get('product_id') or '')
        if not pm_id:
            return None

        name = pm_prod.get('name') or pm_prod.get('title') or 'Unknown'
        ean = pm_prod.get('ean') or pm_prod.get('barcode') or ''
        brand = pm_prod.get('brand') or pm_prod.get('marque') or ''

        # Check if already imported
        already = bool(self.env['product.product'].search([
            ('pm_external_id', '=', pm_id),
        ], limit=1))

        # Extract suppliers
        suppliers = self._extract_pm_suppliers(pm_prod)
        best_price = 0
        best_supplier = ''
        total_stock = 0

        for s in suppliers:
            total_stock += s.get('stock', 0)
            if s.get('is_best') or (s.get('price', 0) > 0 and (best_price == 0 or s['price'] < best_price)):
                best_price = s['price']
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
            'supplier_data_json': json.dumps(suppliers),
        }

        # Fill supplier columns (up to 3)
        for i, s in enumerate(suppliers[:3], 1):
            vals[f'supplier_{i}_name'] = s.get('name', '')
            vals[f'supplier_{i}_price'] = s.get('price', 0)
            vals[f'supplier_{i}_stock'] = s.get('stock', 0)

        return vals

    def _extract_pm_suppliers(self, pm_prod):
        """Extract supplier info from PM product data."""
        suppliers = []
        raw = pm_prod.get('suppliers') or pm_prod.get('tarification') or pm_prod.get('pricing') or []
        if isinstance(raw, list):
            for s in raw:
                suppliers.append({
                    'name': s.get('name') or s.get('supplier_name') or s.get('supplier') or 'Unknown',
                    'price': float(s.get('price') or s.get('prix') or 0),
                    'stock': int(s.get('stock') or s.get('quantity') or 0),
                    'moq': int(s.get('moq') or s.get('min_qty') or 1),
                    'is_best': bool(s.get('is_best') or s.get('best')),
                })
        return suppliers

    def _odoo_data_to_line(self, data):
        """Convert a dict from product.read() to wizard line values.

        Uses dict data instead of record access to avoid triggering
        broken related fields from other modules.
        """
        return {
            'source': 'odoo',
            'odoo_product_id': data['id'],
            'name': data.get('name') or '',
            'brand': data.get('pm_brand') or '',
            'barcode': data.get('barcode') or '',
            'best_price': data.get('standard_price') or 0,
            'best_supplier': '',
            'total_stock': 0,
            'supplier_count': 0,
            'already_imported': bool(data.get('pm_external_id')),
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

        # Refresh search to update already_imported flags
        self.action_search()

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
        # Build product vals from API data + field mapping
        api = self.config_id._get_api_client()
        try:
            pm_data = api.get_product(line.pm_id)
        except ProductsManagerAPIError:
            pm_data = {}

        # Base vals from line data
        vals = {
            'name': line.name,
            'barcode': line.barcode or False,
            'standard_price': line.best_price,
            'pm_external_id': line.pm_id,
            'pm_brand': line.brand or False,
            'pm_last_sync': fields.Datetime.now(),
        }

        # Apply field mappings if PM data available
        if mappings and pm_data:
            mapped_vals = mappings.apply_mapping(pm_data)
            vals.update(mapped_vals)

        # Ensure pm fields are preserved
        vals['pm_external_id'] = line.pm_id
        vals['pm_last_sync'] = fields.Datetime.now()

        # Create the product
        product = self.env['product.product'].create(vals)

        # Create supplier info records
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
