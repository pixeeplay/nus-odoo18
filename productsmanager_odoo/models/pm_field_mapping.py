from odoo import api, fields, models, _


class PmFieldMapping(models.Model):
    _name = 'pm.field.mapping'
    _description = 'Products Manager Field Mapping'
    _order = 'sequence, id'

    config_id = fields.Many2one('pm.config', string='Configuration', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(compute='_compute_name', store=True)
    pm_field = fields.Selection([
        ('name', 'Name'),
        ('brand', 'Brand'),
        ('manufacturer', 'Manufacturer'),
        ('ean', 'EAN'),
        ('barcode', 'Barcode'),
        ('category', 'Category'),
        ('best_price', 'Best Purchase Price'),
        ('description', 'Description'),
        ('weight', 'Weight'),
        ('images', 'Images'),
    ], string='PM Field', required=True)
    odoo_field = fields.Selection([
        ('name', 'Product Name'),
        ('default_code', 'Internal Reference'),
        ('barcode', 'Barcode'),
        ('list_price', 'Sales Price'),
        ('standard_price', 'Cost Price'),
        ('description', 'Description'),
        ('weight', 'Weight'),
        ('volume', 'Volume'),
        ('categ_id', 'Product Category'),
        ('image_1920', 'Image'),
    ], string='Odoo Field', required=True)
    is_active = fields.Boolean(default=True)

    @api.depends('pm_field', 'odoo_field')
    def _compute_name(self):
        pm_labels = dict(self._fields['pm_field'].selection)
        odoo_labels = dict(self._fields['odoo_field'].selection)
        for rec in self:
            pm = pm_labels.get(rec.pm_field, rec.pm_field or '')
            odoo = odoo_labels.get(rec.odoo_field, rec.odoo_field or '')
            rec.name = f'{pm} → {odoo}'

    def apply_mapping(self, pm_data):
        """Apply all active mappings to transform PM data into Odoo product vals."""
        vals = {}
        for mapping in self.filtered('is_active'):
            value = pm_data.get(mapping.pm_field)
            if value is None:
                continue
            # Skip binary fields — images are handled separately via
            # _download_pm_images (they need download + base64 encoding)
            if mapping.odoo_field == 'image_1920':
                continue
            if mapping.odoo_field in ('standard_price', 'list_price', 'weight', 'volume'):
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    continue
            vals[mapping.odoo_field] = value
        return vals
