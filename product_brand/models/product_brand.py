# Copyright 2009 NetAndCo (<http://www.netandco.net>).
# Copyright 2011 Akretion Benoît Guillot <benoit.guillot@akretion.com>
# Copyright 2014 prisnet.ch Seraphine Lantible <s.lantible@gmail.com>
# Copyright 2016 Serpent Consulting Services Pvt. Ltd.
# Copyright 2018 Daniel Campos <danielcampos@avanzosc.es>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo import api, fields, models


class ProductBrand(models.Model):
    _name = "product.brand"
    _description = "Product Brand"
    _order = "name"

    name = fields.Char("Brand Name", required=True)
    description = fields.Text(translate=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        help="Select a partner for this brand if any.",
        ondelete="restrict",
    )
    logo = fields.Binary("Logo File")
    product_ids = fields.One2many(
        "product.template", "product_brand_id", string="Brand Products"
    )
    products_count = fields.Integer(
        string="Number of products", compute="_compute_products_count"
    )
    # === Brand Alias System for import normalization ===
    aliases = fields.Text(
        string="Aliases",
        help="Comma-separated list of alternative names for this brand used by suppliers. "
             "Example: SMG,Samsungpro,SAMSUNG ELECTRONICS",
    )
    manufacturer = fields.Char(
        string="Manufacturer/Group",
        help="Parent company or manufacturer group (e.g. Samsung Electronics, BSH, SEB)",
    )

    @api.depends("product_ids")
    def _compute_products_count(self):
        product_model = self.env["product.template"]
        groups = product_model.read_group(
            [("product_brand_id", "in", self.ids)],
            ["product_brand_id"],
            ["product_brand_id"],
            lazy=False,
        )
        data = {group["product_brand_id"][0]: group["__count"] for group in groups}
        for brand in self:
            brand.products_count = data.get(brand.id, 0)

    @api.model
    def find_by_name_or_alias(self, brand_name, create_if_not_found=False):
        """
        Search for a brand by exact name or by alias.
        
        Args:
            brand_name: The brand name to search for
            create_if_not_found: If True, create the brand if not found
            
        Returns:
            product.brand recordset (single record or empty)
        """
        if not brand_name:
            return self.browse()
        
        brand_name_clean = brand_name.strip()
        brand_name_upper = brand_name_clean.upper()
        
        # 1. First try exact match on name (case-insensitive)
        brand = self.search([('name', '=ilike', brand_name_clean)], limit=1)
        if brand:
            return brand
        
        # 2. Search in aliases
        all_brands = self.search([('aliases', '!=', False)])
        for brand in all_brands:
            if brand.aliases:
                alias_list = [a.strip().upper() for a in brand.aliases.split(',') if a.strip()]
                if brand_name_upper in alias_list:
                    return brand
        
        # 3. Not found - create if requested
        if create_if_not_found:
            return self.create({'name': brand_name_clean})
        
        return self.browse()

    @api.model
    def get_or_create_brand(self, brand_name, manufacturer=None):
        """
        Get existing brand by name/alias or create a new one.
        
        Args:
            brand_name: The brand name
            manufacturer: Optional manufacturer/group name
            
        Returns:
            product.brand record
        """
        if not brand_name:
            return self.browse()
        
        brand = self.find_by_name_or_alias(brand_name, create_if_not_found=False)
        if brand:
            return brand
        
        # Create new brand
        vals = {'name': brand_name.strip()}
        if manufacturer and manufacturer.strip() and manufacturer.strip().upper() != 'INCONNU':
            vals['manufacturer'] = manufacturer.strip()
        
        return self.create(vals)
