import logging
from odoo import models, fields, api

_LOGGER = logging.getLogger(__name__)

class ProductProcessor(models.TransientModel):
    _name = 'code2asin.product.processor'
    _description = 'Code2ASIN Product Processor'

    def process_product_row(self, row, mapping, headers, row_number, import_session_id, log_model, config):
        """Traite une ligne de données pour créer/mettre à jour un produit."""
        try:
            # Extraire les données de base
            raw_barcode = row[mapping.get('barcode', -1)] if mapping.get('barcode', -1) >= 0 and len(row) > mapping.get('barcode', -1) else ''
            name = row[mapping.get('name', -1)] if mapping.get('name', -1) >= 0 and len(row) > mapping.get('name', -1) else ''
            
            # Utiliser le helper de validation pour les nouvelles fonctionnalités
            validation_helper = self.env['code2asin.validation.helper']
            
            # NOUVELLE FONCTIONNALITÉ 1: Skip existing EAN
            if config.skip_existing_ean and raw_barcode:
                skip_result = validation_helper.should_skip_existing_product(raw_barcode, config.skip_existing_ean)
                if skip_result['should_skip']:
                    log_model.create({
                        'name': f"Ligne {row_number}: Produit ignoré - {skip_result['reason']} ({skip_result['existing_product'].name if skip_result['existing_product'] else 'N/A'})",
                        'log_type': 'info',
                        'import_session_id': import_session_id
                    })
                    return 'skipped'
            
            # NOUVELLE FONCTIONNALITÉ 2: Traitement des EAN multiples amélioré
            product = None
            matched_ean = None
            primary_ean = None
            
            if raw_barcode:
                # Rechercher un produit existant avec l'un des EAN multiples
                search_result = validation_helper.find_product_by_multiple_eans(raw_barcode)
                if search_result['found']:
                    product = search_result['product']
                    matched_ean = search_result['matched_ean']
                    primary_ean = search_result['primary_ean']
                    
                    log_model.create({
                        'name': f"Ligne {row_number}: Produit existant trouvé avec EAN {matched_ean} - {len(search_result['all_eans'])} EAN total(s) dans la cellule",
                        'log_type': 'info',
                        'import_session_id': import_session_id
                    })
                else:
                    # Aucun produit trouvé, utiliser le premier EAN pour création
                    primary_ean = search_result['primary_ean']
                    if len(search_result['all_eans']) > 1:
                        log_model.create({
                            'name': f"Ligne {row_number}: EAN multiples détectés ({len(search_result['all_eans'])} codes), utilisation du premier pour nouveau produit: {primary_ean}",
                            'log_type': 'info',
                            'import_session_id': import_session_id
                        })
            
            # Validation des données minimales
            if not primary_ean and not name:
                log_model.create({
                    'name': f"Ligne {row_number}: Ignorée - pas de code-barres ni de nom",
                    'log_type': 'warning',
                    'import_session_id': import_session_id
                })
                return 'skipped'
            
            # Préparer les données du produit
            product_data = {}
            
            # Nom du produit
            if config.import_name and name:
                if config.name_update_mode == 'replace' or (not product or not product.name):
                    product_data['name'] = name[:1000]  # Limiter la longueur
            
            # Code-barres (utiliser le premier EAN valide)
            if primary_ean:
                product_data['barcode'] = primary_ean
            
            # Référence interne (default_code)
            default_code = row[mapping.get('default_code', -1)] if mapping.get('default_code', -1) >= 0 and len(row) > mapping.get('default_code', -1) else ''
            if config.import_default_code and default_code:
                if config.default_code_update_mode == 'replace' or (not product or not product.default_code):
                    product_data['default_code'] = default_code[:100]  # Limiter la longueur
            
            # Prix
            price_str = row[mapping.get('list_price', -1)] if mapping.get('list_price', -1) >= 0 and len(row) > mapping.get('list_price', -1) else ''
            if config.import_price and price_str:
                try:
                    price = float(price_str.replace(',', '.'))
                    if price > 0 and (config.price_update_mode == 'replace' or (not product or product.list_price == 0)):
                        product_data['list_price'] = price
                except ValueError:
                    log_model.create({
                        'name': f"Ligne {row_number}: Prix invalide '{price_str}'",
                        'log_type': 'warning',
                        'import_session_id': import_session_id
                    })
            
            # Poids
            weight_str = row[mapping.get('weight', -1)] if mapping.get('weight', -1) >= 0 and len(row) > mapping.get('weight', -1) else ''
            if config.import_weight and weight_str:
                try:
                    weight_grams = float(weight_str.replace(',', '.'))
                    weight_kg = weight_grams / 1000  # Convertir grammes en kg
                    if weight_kg > 0 and (config.weight_update_mode == 'replace' or (not product or product.weight == 0)):
                        product_data['weight'] = weight_kg
                except ValueError:
                    log_model.create({
                        'name': f"Ligne {row_number}: Poids invalide '{weight_str}'",
                        'log_type': 'warning',
                        'import_session_id': import_session_id
                    })
            
            # Dimensions - TODO: À implémenter plus tard
            # Les champs product_length, product_width, product_height n'existent pas dans Odoo 18 standard
            # Il faudra créer des champs personnalisés ou utiliser un module tiers
            if config.import_dimensions:
                log_model.create({
                    'name': f"Ligne {row_number}: Import dimensions ignoré - fonctionnalité à implémenter",
                    'log_type': 'info',
                    'import_session_id': import_session_id
                })
            
            # Marque - vérifier si le modèle product.brand existe
            brand_str = row[mapping.get('brand', -1)] if mapping.get('brand', -1) >= 0 and len(row) > mapping.get('brand', -1) else ''
            if config.import_brand and brand_str:
                try:
                    # Vérifier si le modèle product.brand existe
                    if 'product.brand' in self.env:
                        if config.brand_update_mode == 'replace' or (not product or not hasattr(product, 'brand') or not product.brand):
                            # Créer ou trouver la marque
                            brand = self.env['product.brand'].search([('name', '=', brand_str)], limit=1)
                            if not brand:
                                brand = self.env['product.brand'].create({'name': brand_str})
                            product_data['brand_id'] = brand.id
                    else:
                        # Le modèle product.brand n'existe pas, ignorer silencieusement
                        pass
                except Exception as e:
                    # Erreur avec le modèle product.brand, ignorer et continuer
                    _LOGGER.warning(f"Ligne {row_number}: Impossible d'importer la marque '{brand_str}' - modèle product.brand non disponible: {e}")
                    pass
            
            # Couleur
            color_str = row[mapping.get('color', -1)] if mapping.get('color', -1) >= 0 and len(row) > mapping.get('color', -1) else ''
            if config.import_color and color_str:
                if config.color_update_mode == 'replace' or (not product or not hasattr(product, 'color') or not product.color):
                    product_data['color'] = color_str[:50]  # Limiter la longueur
            
            # Description/Fonctionnalités
            features_str = row[mapping.get('features', -1)] if mapping.get('features', -1) >= 0 and len(row) > mapping.get('features', -1) else ''
            if config.import_description and features_str:
                if config.description_update_mode == 'replace' or (not product or not product.description_sale):
                    product_data['description_sale'] = features_str
            
            # Si aucune donnée à traiter
            if not product_data:
                log_model.create({
                    'name': f"Ligne {row_number}: Aucune donnée à importer",
                    'log_type': 'warning',
                    'import_session_id': import_session_id
                })
                return 'skipped'
            
            # Créer ou mettre à jour le produit
            if product:
                # Mise à jour du produit existant
                product.write(product_data)
                log_model.create({
                    'name': f"Ligne {row_number}: Produit '{product.name}' mis à jour (EAN correspondant: {matched_ean})",
                    'log_type': 'info',
                    'import_session_id': import_session_id
                })
                self.env.cr.commit()
                
                # Traitement des images
                if config.import_images:
                    self._process_product_images(product, row, mapping, row_number, import_session_id, log_model, config)
                
                return 'updated'
            else:
                # Création d'un nouveau produit
                # S'assurer qu'on a au moins un nom
                if 'name' not in product_data:
                    product_data['name'] = f"Produit {primary_ean or 'sans code'}"
                
                product = self.env['product.template'].create(product_data)
                log_model.create({
                    'name': f"Ligne {row_number}: Nouveau produit '{product.name}' créé avec EAN {primary_ean}",
                    'log_type': 'info',
                    'import_session_id': import_session_id
                })
                self.env.cr.commit()
                
                # Traitement des images
                if config.import_images:
                    self._process_product_images(product, row, mapping, row_number, import_session_id, log_model, config)
                
                return 'created'
                
        except Exception as e:
            log_model.create({
                'name': f"Ligne {row_number}: ERREUR - {str(e)}",
                'log_type': 'error',
                'import_session_id': import_session_id
            })
            _LOGGER.error(f"Erreur ligne {row_number}: {e}")
            return 'error'

    def _process_product_images(self, product, row, mapping, row_number, import_session_id, log_model, config):
        """Traite les images d'un produit."""
        try:
            images_str = row[mapping.get('images', -1)] if mapping.get('images', -1) >= 0 and len(row) > mapping.get('images', -1) else ''
            
            if not images_str:
                return
            
            # Vérifier si on doit traiter les images
            if config.images_update_mode == 'update' and product.image_1920:
                return  # Skip si le produit a déjà une image
            
            # Utiliser l'helper d'images
            image_helper = self.env['code2asin.image.import.helper']
            image_helper.import_images_for_product(product, images_str, import_session_id, log_model)
            
        except Exception as e:
            log_model.create({
                'name': f"Ligne {row_number}: Erreur import images - {str(e)}",
                'log_type': 'warning',
                'import_session_id': import_session_id
            })

    def _validate_and_clean_barcode(self, barcode):
        """DEPRECATED: Utiliser validation_helper.validate_and_clean_barcode() à la place.
        
        Cette méthode est conservée pour compatibilité mais il est recommandé
        d'utiliser directement le helper de validation."""
        validation_helper = self.env['code2asin.validation.helper']
        return validation_helper.validate_and_clean_barcode(barcode)
