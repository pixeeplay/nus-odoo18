import base64
import requests
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_LOGGER = logging.getLogger(__name__)

class Code2AsinImageImportHelper(models.TransientModel):
    _name = 'code2asin.image.import.helper'
    _description = 'Code2ASIN Image Import Helper'
    
    def download_image_from_url(self, url, timeout=10):
        """Télécharge une image depuis une URL et retourne les données en base64."""
        try:
            if not url or not url.strip():
                return None
                
            url = url.strip()
            
            # Vérifier que l'URL commence par http ou https
            if not url.startswith(('http://', 'https://')):
                _LOGGER.warning(f"URL invalide (ne commence pas par http/https): {url}")
                return None
            
            # Headers pour simuler un navigateur
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept-Language': 'en-US,en;q=0.9',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            }
            
            # Télécharger l'image avec timeout
            response = requests.get(url, headers=headers, timeout=timeout, stream=True)
            response.raise_for_status()
            
            # Vérifier le type de contenu
            content_type = response.headers.get('content-type', '').lower()
            if not content_type.startswith('image/'):
                _LOGGER.warning(f"Type de contenu non valide pour l'image {url}: {content_type}")
                return None
            
            # Vérifier la taille de l'image (limite à 5MB)
            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > 5 * 1024 * 1024:
                _LOGGER.warning(f"Image trop grande {url}: {content_length} bytes")
                return None
            
            # Lire les données
            image_data = response.content
            
            # Vérifier que les données ne sont pas vides
            if not image_data:
                _LOGGER.warning(f"Données d'image vides pour {url}")
                return None
            
            # Vérifier la taille des données lues (5MB max)
            if len(image_data) > 5 * 1024 * 1024:
                _LOGGER.warning(f"Image téléchargée trop grande {url}: {len(image_data)} bytes")
                return None
            
            # Encoder en base64
            return base64.b64encode(image_data).decode('utf-8')
            
        except requests.exceptions.Timeout:
            _LOGGER.warning(f"Timeout lors du téléchargement de l'image: {url}")
            return None
        except requests.exceptions.RequestException as e:
            _LOGGER.warning(f"Erreur lors du téléchargement de l'image {url}: {str(e)}")
            return None
        except Exception as e:
            _LOGGER.error(f"Erreur inattendue lors du téléchargement de l'image {url}: {str(e)}")
            return None
    
    def parse_image_urls(self, images_string):
        """Parse une chaîne d'URLs d'images séparées par des virgules, points-virgules ou espaces."""
        if not images_string or not images_string.strip():
            return []
        
        # Séparer par différents délimiteurs possibles
        separators = [',', ';', '\n', '\r\n', '|']
        urls = [images_string.strip()]
        
        for separator in separators:
            new_urls = []
            for url in urls:
                new_urls.extend([u.strip() for u in url.split(separator) if u.strip()])
            urls = new_urls
        
        # Filtrer les URLs valides
        valid_urls = []
        for url in urls:
            if url and url.startswith(('http://', 'https://')):
                valid_urls.append(url)
        
        return valid_urls[:10]  # Limiter à 10 images maximum
    
    def import_images_for_product(self, product, images_string, import_session_id, log_model):
        """Importe les images pour un produit selon les spécifications Odoo ecommerce."""
        try:
            # Parser les URLs d'images
            image_urls = self.parse_image_urls(images_string)
            
            if not image_urls:
                log_model.create({
                    'name': f"Produit {product.barcode or product.name}: Aucune URL d'image valide trouvée",
                    'log_type': 'warning',
                    'import_session_id': import_session_id
                })
                return
            
            log_model.create({
                'name': f"Produit {product.barcode or product.name}: Traitement de {len(image_urls)} images",
                'log_type': 'info',
                'import_session_id': import_session_id
            })
            
            images_processed = 0
            images_failed = 0
            
            for index, url in enumerate(image_urls):
                try:
                    # Télécharger l'image
                    image_data = self.download_image_from_url(url)
                    
                    if image_data:
                        if index == 0:
                            # Première image = image principale du produit
                            if not product.image_1920:  # Seulement si pas d'image existante
                                product.image_1920 = image_data
                                log_model.create({
                                    'name': f"Produit {product.barcode or product.name}: Image principale assignée",
                                    'log_type': 'info',
                                    'import_session_id': import_session_id
                                })
                        
                        # Toutes les images (y compris la première) vont dans product.image pour ecommerce
                        # Détecter si c'est un product.product ou product.template
                        if hasattr(product, 'product_tmpl_id'):
                            # C'est un product.product
                            template_id = product.product_tmpl_id.id
                        else:
                            # C'est un product.template
                            template_id = product.id
                        
                        self.env['product.image'].create({
                            'name': f'Image {index + 1}',
                            'product_tmpl_id': template_id,
                            'image_1920': image_data,
                        })
                        
                        images_processed += 1
                        
                    else:
                        images_failed += 1
                        log_model.create({
                            'name': f"Produit {product.barcode or product.name}: Échec téléchargement image {index + 1}",
                            'log_type': 'warning',
                            'import_session_id': import_session_id
                        })
                        
                except Exception as e:
                    images_failed += 1
                    log_model.create({
                        'name': f"Produit {product.barcode or product.name}: Erreur image {index + 1}: {str(e)}",
                        'log_type': 'error',
                        'import_session_id': import_session_id
                    })
                    _LOGGER.error(f"Erreur traitement image {url}: {str(e)}")
            
            # Log final du résultat
            if images_processed > 0:
                log_model.create({
                    'name': f"✓ Produit {product.barcode or product.name}: {images_processed} images importées avec succès",
                    'log_type': 'success',
                    'import_session_id': import_session_id
                })
            
            if images_failed > 0:
                log_model.create({
                    'name': f"⚠ Produit {product.barcode or product.name}: {images_failed} images ont échoué",
                    'log_type': 'warning',
                    'import_session_id': import_session_id
                })
                
        except Exception as e:
            log_model.create({
                'name': f"❌ Erreur critique import images pour {product.barcode or product.name}: {str(e)}",
                'log_type': 'error',
                'import_session_id': import_session_id
            })
            _LOGGER.error(f"Erreur critique import images: {str(e)}")
