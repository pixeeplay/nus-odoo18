import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_LOGGER = logging.getLogger(__name__)

class ValidationHelper(models.TransientModel):
    _name = 'code2asin.validation.helper'
    _description = 'Code2ASIN Validation Helper'

    def parse_csv_with_encoding(self, csv_data):
        """Parse le CSV en essayant différents encodages."""
        # Liste des encodages à essayer dans l'ordre
        encodings = ['utf-8', 'iso-8859-1', 'windows-1252', 'cp1252', 'latin1']
        
        for encoding in encodings:
            try:
                decoded_data = csv_data.decode(encoding)
                _LOGGER.info(f"Fichier CSV décodé avec succès en {encoding}")
                return decoded_data.splitlines()
            except UnicodeDecodeError:
                _LOGGER.debug(f"Échec de décodage avec {encoding}, essai suivant...")
                continue
        
        # Si aucun encodage ne fonctionne, essayer avec 'errors=replace'
        try:
            decoded_data = csv_data.decode('utf-8', errors='replace')
            _LOGGER.warning("Décodage UTF-8 avec remplacement de caractères invalides")
            return decoded_data.splitlines()
        except Exception as e:
            raise UserError(f"Impossible de décoder le fichier CSV. Erreur: {str(e)}")

    def map_csv_columns(self, headers):
        """Mappe les colonnes CSV vers les champs Odoo."""
        mapping = {}
        
        # Mapping des colonnes CSV vers les champs
        column_mapping = {
            'EAN': 'barcode',
            'UPC': 'barcode_alt',
            'ASIN': 'default_code',
            'Code d\'importation': 'default_code_alt',
            'Titre': 'name',
            'Prix Amazon (€)': 'list_price',
            'Poids de l\'article (g)': 'weight',
            'Longueur de l\'article (cm)': 'length',
            'Largeur de l\'article (cm)': 'width',
            'Hauteur de l\'article (cm)': 'height',
            'Marque': 'brand',
            'Couleur': 'color',
            'Fonctionnalités': 'features',
            'Images': 'images'
        }
        
        # Créer le mapping basé sur les en-têtes trouvées
        for i, header in enumerate(headers):
            header_clean = header.strip('"')
            if header_clean in column_mapping:
                mapping[column_mapping[header_clean]] = i
                
        return mapping

    def validate_and_clean_barcode(self, barcode):
        """Valide et nettoie un code-barres pour éviter les erreurs PostgreSQL.
        
        Gère les codes-barres multiples séparés par des virgules (ex: Code2ASIN UPC).
        Retourne uniquement le premier code-barres valide.
        """
        if not barcode:
            return ''
        
        # Convertir en string et nettoyer
        barcode = str(barcode).strip()
        
        # GESTION DES CODES MULTIPLES : Code2ASIN peut mettre plusieurs codes séparés par des virgules
        if ',' in barcode:
            _LOGGER.info(f"Codes-barres multiples détectés: {barcode[:100]}...")
            # Prendre le premier code non vide
            codes = [code.strip() for code in barcode.split(',') if code.strip()]
            if codes:
                barcode = codes[0]
                _LOGGER.info(f"Premier code-barres utilisé: {barcode}")
            else:
                return ''
        
        # LIMITE CRITIQUE : PostgreSQL btree index limite à ~2700 caractères
        # Mais pour les EAN/UPC, limite pratique à 50 caractères maximum
        if len(barcode) > 50:
            _LOGGER.warning(f"Code-barres trop long ({len(barcode)} chars), tronqué à 50: {barcode[:50]}...")
            barcode = barcode[:50]
        
        # Retirer les caractères non-imprimables et espaces
        barcode = ''.join(char for char in barcode if char.isprintable() and char != ' ')
        
        # Vérification finale : codes-barres EAN/UPC sont numériques
        if barcode and not barcode.isdigit():
            _LOGGER.warning(f"Code-barres non numérique détecté: {barcode[:20]}... - conservé tel quel")
        
        return barcode

    def parse_multiple_eans(self, ean_string):
        """Parse une chaîne contenant plusieurs EAN séparés par des virgules.
        
        Retourne une liste de tous les EAN valides trouvés.
        """
        if not ean_string:
            return []
        
        # Convertir en string et nettoyer
        ean_string = str(ean_string).strip()
        
        # Séparer par les virgules et nettoyer chaque EAN
        ean_codes = []
        if ',' in ean_string:
            raw_codes = [code.strip() for code in ean_string.split(',') if code.strip()]
            _LOGGER.info(f"EAN multiples détectés: {len(raw_codes)} codes à traiter")
        else:
            raw_codes = [ean_string]
        
        # Valider et nettoyer chaque EAN
        for raw_code in raw_codes:
            # Appliquer les mêmes règles de validation que validate_and_clean_barcode
            # mais sans gérer les virgules (déjà fait)
            clean_code = str(raw_code).strip()
            
            # Limite de longueur
            if len(clean_code) > 50:
                _LOGGER.warning(f"EAN trop long ({len(clean_code)} chars), tronqué: {clean_code[:50]}...")
                clean_code = clean_code[:50]
            
            # Retirer les caractères non-imprimables et espaces
            clean_code = ''.join(char for char in clean_code if char.isprintable() and char != ' ')
            
            # Vérification finale
            if clean_code and not clean_code.isdigit():
                _LOGGER.warning(f"EAN non numérique détecté: {clean_code[:20]}... - conservé tel quel")
            
            if clean_code:
                ean_codes.append(clean_code)
        
        _LOGGER.info(f"EAN validés: {len(ean_codes)} codes valides sur {len(raw_codes)} codes initiaux")
        return ean_codes

    def find_product_by_multiple_eans(self, ean_string):
        """Recherche un produit existant parmi tous les EAN fournis.
        
        Args:
            ean_string: Chaîne contenant un ou plusieurs EAN séparés par des virgules
            
        Returns:
            dict: {
                'found': bool,
                'product': product.template record ou False,
                'matched_ean': EAN qui a matché ou None,
                'all_eans': liste de tous les EAN validés,
                'primary_ean': premier EAN valide (pour création si nécessaire)
            }
        """
        result = {
            'found': False,
            'product': False,
            'matched_ean': None,
            'all_eans': [],
            'primary_ean': None
        }
        
        # Parser tous les EAN
        all_eans = self.parse_multiple_eans(ean_string)
        result['all_eans'] = all_eans
        
        if not all_eans:
            return result
        
        result['primary_ean'] = all_eans[0]  # Premier EAN pour création éventuelle
        
        # Rechercher un produit existant avec l'un de ces EAN
        for ean in all_eans:
            existing_product = self.env['product.template'].search([
                ('barcode', '=', ean)
            ], limit=1)
            
            if existing_product:
                _LOGGER.info(f"Produit trouvé avec EAN {ean}: {existing_product.name}")
                result['found'] = True
                result['product'] = existing_product
                result['matched_ean'] = ean
                return result
        
        _LOGGER.info(f"Aucun produit trouvé pour les EAN: {', '.join(all_eans[:3])}{'...' if len(all_eans) > 3 else ''}")
        return result

    def should_skip_existing_product(self, ean_string, skip_existing_ean):
        """Détermine si un produit doit être ignoré selon l'option skip_existing_ean.
        
        Args:
            ean_string: Chaîne contenant un ou plusieurs EAN
            skip_existing_ean: Boolean indiquant si on doit ignorer les EAN existants
            
        Returns:
            dict: {
                'should_skip': bool,
                'reason': str,
                'existing_product': product.template ou False
            }
        """
        if not skip_existing_ean:
            return {'should_skip': False, 'reason': 'Skip option disabled', 'existing_product': False}
        
        # Vérifier si un produit existe avec l'un des EAN
        search_result = self.find_product_by_multiple_eans(ean_string)
        
        if search_result['found']:
            return {
                'should_skip': True,
                'reason': f"Product exists with EAN {search_result['matched_ean']}",
                'existing_product': search_result['product']
            }
        
        return {'should_skip': False, 'reason': 'No existing product found', 'existing_product': False}

    def check_existing_ean(self, ean_code):
        """Vérifie si un EAN existe déjà dans la base de données."""
        if not ean_code:
            return False
            
        # Nettoyer le code EAN
        clean_ean = self.validate_and_clean_barcode(ean_code)
        if not clean_ean:
            return False
            
        # Rechercher dans product.template
        existing_product = self.env['product.template'].search([
            ('barcode', '=', clean_ean)
        ], limit=1)
        
        return bool(existing_product)
