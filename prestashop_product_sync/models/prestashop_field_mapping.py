from odoo import models, fields, api, _


# Default mapping definitions: (ps_field, ps_label, odoo_field, odoo_label, field_type, active)
DEFAULT_MAPPINGS = [
    ('name', 'Product Name', 'name', 'Product Name', 'text', True),
    ('reference', 'Reference / SKU', 'default_code', 'Internal Reference', 'text', True),
    ('price', 'Price (HT)', 'list_price', 'Sales Price', 'float', True),
    ('wholesale_price', 'Wholesale Price', 'standard_price', 'Cost', 'float', True),
    ('weight', 'Weight', 'weight', 'Weight', 'float', True),
    ('ean13', 'EAN13 / Barcode', 'barcode', 'Barcode', 'text', True),
    ('description', 'Full Description (HTML)', 'prestashop_description_html', 'PS Full Description', 'html', True),
    ('description_short', 'Short Description (HTML)', 'prestashop_description_short_html', 'PS Short Description', 'html', True),
    ('description', 'Full Description (text)', 'description', 'Internal Description', 'text', True),
    ('description_short', 'Short Description (text)', 'description_sale', 'Sales Description', 'text', True),
    ('meta_title', 'Meta Title', 'prestashop_meta_title', 'PS Meta Title', 'text', True),
    ('meta_description', 'Meta Description', 'prestashop_meta_description', 'PS Meta Description', 'text', True),
    ('id_manufacturer', 'Manufacturer', 'prestashop_manufacturer', 'PS Manufacturer', 'relation', True),
    ('active', 'Active', 'prestashop_active', 'Active in PrestaShop', 'boolean', True),
    ('link_rewrite', 'URL Slug', 'prestashop_url', 'PrestaShop URL', 'text', True),
    ('id_category_default', 'Default Category', 'categ_id', 'Product Category', 'relation', True),
    ('associations.images', 'Product Images', 'image_1920', 'Product Image', 'image', True),
    ('associations.product_features', 'Features / Characteristics', 'product_attributes', 'Product Attributes', 'relation', True),
    ('stock_availables', 'Stock Quantity', 'qty_available', 'Quantity On Hand', 'stock', True),
    ('ecotax', 'Eco-Tax', 'prestashop_ecotax', 'PS Eco-Tax', 'float', True),
    ('id_tax_rules_group', 'Tax Rules Group', 'prestashop_tax_rules_group_id', 'PS Tax Group', 'relation', True),
]


class PrestaShopFieldMapping(models.Model):
    _name = 'prestashop.field.mapping'
    _description = 'PrestaShop Field Mapping'
    _order = 'sequence, id'

    instance_id = fields.Many2one(
        'prestashop.instance', 'Instance', required=True,
        ondelete='cascade', index=True,
    )
    sequence = fields.Integer('Sequence', default=10)
    ps_field_name = fields.Char('PS Field (Technical)', required=True)
    ps_field_label = fields.Char('PrestaShop Field')
    odoo_field_name = fields.Char('Odoo Field (Technical)', required=True)
    odoo_field_label = fields.Char('Odoo Field')
    field_type = fields.Selection([
        ('text', 'Text'),
        ('html', 'HTML'),
        ('float', 'Number'),
        ('boolean', 'Boolean'),
        ('image', 'Image'),
        ('relation', 'Relation'),
        ('stock', 'Stock'),
    ], string='Type', default='text')
    active = fields.Boolean('Active', default=True)
    notes = fields.Char('Notes')

    def name_get(self):
        return [(r.id, '%s → %s' % (r.ps_field_label, r.odoo_field_label)) for r in self]

    @api.model
    def _create_defaults_for_instance(self, instance_id):
        """Create the default field mappings for an instance."""
        existing = self.search([('instance_id', '=', instance_id)])
        existing_keys = set(
            (m.ps_field_name, m.odoo_field_name) for m in existing
        )

        seq = 10
        for ps_field, ps_label, odoo_field, odoo_label, ftype, is_active in DEFAULT_MAPPINGS:
            if (ps_field, odoo_field) not in existing_keys:
                self.create({
                    'instance_id': instance_id,
                    'sequence': seq,
                    'ps_field_name': ps_field,
                    'ps_field_label': ps_label,
                    'odoo_field_name': odoo_field,
                    'odoo_field_label': odoo_label,
                    'field_type': ftype,
                    'active': is_active,
                })
            seq += 10
