# -*- coding: utf-8 -*-
"""
Extension du modèle product.brand pour ajouter le champ aliases.

Ce champ permet de stocker des noms alternatifs pour les marques,
facilitant la correspondance entre les différentes appellations
utilisées par les fournisseurs.
"""
from odoo import api, fields, models

import logging
_logger = logging.getLogger(__name__)


class ProductBrandExtension(models.Model):
    _inherit = "product.brand"

    aliases = fields.Char(
        string="Alias",
        help="Noms alternatifs séparés par virgule (ex: SMG,SAMS pour Samsung). "
             "Utilisés pour faire correspondre les marques des fichiers fournisseurs.",
    )

    @api.model
    def find_by_name_or_alias(self, name, create_if_not_found=False):
        """Recherche une marque par son nom exact ou ses alias.
        
        Args:
            name: Nom de la marque à rechercher
            create_if_not_found: Si True, crée la marque si non trouvée
            
        Returns:
            product.brand record ou False
        """
        if not name:
            return self.browse()
        
        name_clean = name.strip()
        name_upper = name_clean.upper()
        
        # 1. Recherche exacte par nom
        brand = self.search([("name", "=ilike", name_clean)], limit=1)
        if brand:
            return brand
        
        # 2. Recherche dans les alias (si le champ existe et n'est pas vide)
        try:
            all_brands = self.search([("aliases", "!=", False)])
            for b in all_brands:
                if b.aliases:
                    alias_list = [a.strip().upper() for a in b.aliases.split(",") if a.strip()]
                    if name_upper in alias_list:
                        return b
        except Exception as e:
            _logger.warning("Erreur recherche alias: %s", e)
        
        # 3. Créer si demandé
        if create_if_not_found:
            try:
                brand = self.create({"name": name_clean})
                _logger.info("Marque créée: %s (id=%d)", name_clean, brand.id)
                return brand
            except Exception as e:
                _logger.warning("Impossible de créer la marque '%s': %s", name_clean, e)
        
        return self.browse()
