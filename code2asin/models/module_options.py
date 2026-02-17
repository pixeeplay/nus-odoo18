import base64
import csv
import io
import logging
import json
import re
import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_LOGGER = logging.getLogger(__name__)
LIMIT_PRODUCTS = 100000


def snake_case(string):
    """Convert a string to snake_case, enlevant les car. speciaux."""
    # Retirer le BOM s'il est présent
    string = string.replace('\ufeff', '')

    # Remplacer les caractères accentués par leur équivalent sans accent
    string = re.sub(r'[éèêë]', 'e', string)
    string = re.sub(r'[àâä]', 'a', string)
    string = re.sub(r'[îï]', 'i', string)
    string = re.sub(r'[ôö]', 'o', string)
    string = re.sub(r'[ùûü]', 'u', string)
    string = re.sub(r'[ç]', 'c', string)

    string = re.sub(r'\s+', '_', string).lower()
    # Supprimer les caractères non alphanumériques sauf les underscores
    string = re.sub(r'[^a-zA-Z0-9_]', '', string.strip().replace('-', '_'))

    return string


def _csv_to_json(csv_file):
    try:
        data = base64.b64decode(csv_file)
        csv_data = data.decode('utf-8')

        csv_reader = csv.DictReader(csv_data.splitlines())

        json_data = []
        for row in csv_reader:
            formatted_row = {snake_case(key): decode_special_characters(value) for key, value in row.items()}
            json_data.append(formatted_row)

        json_dump = json.dumps(json_data, indent=4)
        json_output = json.loads(json_dump)
        return json_output

    except (base64.binascii.Error, UnicodeDecodeError, csv.Error) as e:
        _LOGGER.error(f"Erreur lors de la conversion du CSV en JSON: {e}")
        raise UserError(
            "Erreur lors de la conversion du fichier CSV. Assurez-vous que le fichier est correct et encodé en UTF-8.")


def decode_special_characters(value):
    """Convertir les séquences d'échappement en caractères spéciaux lisibles."""
    if isinstance(value, str):
        try:
            value = value.encode('utf-8').decode('unicode_escape').encode('latin1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError) as e:
            _LOGGER.error(f"Erreur lors du décodage des caractères spéciaux: {e}")
            raise UserError(
                f"Erreur lors du décodage des caractères spéciaux: {e}")

        return value
    return value


def _download_image(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return base64.b64encode(response.content)
    except requests.exceptions.RequestException as e:
        _LOGGER.error(f"Erreur lors du téléchargement de l'image depuis {url}: {e}")
        raise UserError(f"Erreur lors du téléchargement de l'image depuis {url}. Veuillez vérifier l'URL et réessayer.")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    csv_file = fields.Binary(string="CSV File")
    filename = fields.Char(string="Filename")

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()

        csv_file = self.env['ir.config_parameter'].sudo().get_param('code2asin.csv_file', default='')
        res.update(csv_file=csv_file)

        filename = self.env['ir.config_parameter'].sudo().get_param('code2asin.filename', default='')
        res.update(filename=filename)

        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()

        self.env['ir.config_parameter'].sudo().set_param('code2asin.csv_file', self.csv_file)
        self.env['ir.config_parameter'].sudo().set_param('code2asin.filename', self.filename)

    def set_images_from_urls(self, product, urls):
        index = 1
        for url in urls:
            url = url.strip()
            try:
                response = requests.get(url)
                response.raise_for_status()
                # Conversion de l'image téléchargée en base64
                image_base64 = base64.b64encode(response.content)
                # Création de l'enregistrement dans le modèle 'product.image'
                self.env['product.image'].create({
                    'name': f"Image {index} for {product.name}",  # Nom dynamique basé sur l'index et le nom du produit
                    'image_1920': image_base64,
                    'product_tmpl_id': product.product_tmpl_id.id,
                    'sequence': 10 * index  # Ajuster la séquence pour chaque image
                })
                index += 1
            except requests.exceptions.RequestException as e:
                _LOGGER.error(f"Erreur lors du téléchargement de l'image depuis {url}: {e}")
                raise UserError(
                    f"Erreur lors du téléchargement de l'image depuis {url}. Veuillez vérifier l'URL et réessayer.")

    def action_import_products_from_code2asin(self):
        if not self.csv_file:
            raise UserError("Veuillez sélectionner un fichier CSV à importer.")

        _LOGGER.info("Importing products from Code2asin")

        try:
            data = _csv_to_json(self.csv_file)
        except UserError as e:
            _LOGGER.error(f"Erreur lors de la conversion du CSV : {e}")
            raise e  # Renvoyer l'erreur à l'utilisateur

        product_obj = self.env['product.product']
        num_products = 0

        for item in data:
            num_products += 1
            if num_products > LIMIT_PRODUCTS:
                break

            _LOGGER.info(f"----- ➡️ Start creating product : {item.get('titre', '')}  -----")

            try:
                # Gérer les images
                image_urls = item.get('images', '')
                image_data = None

                images_url_array = image_urls.split(',')

                if images_url_array:
                    first_image_url = images_url_array[0].strip()
                    if first_image_url:
                        image_data = _download_image(first_image_url)

                price_str = item.get('prix_buy_box_nouvelle_', '')
                price = float(price_str) if price_str else 0.0

                weight_str = item.get('poids_de_larticle_g', '')
                weight = float(weight_str) / 1000 if weight_str else 0.0

                barcode = item.get('ean', '').split(',')[0].strip()
                _LOGGER.info("🔄 Search existing product...")

                existing_product = product_obj.search([('barcode', '=', barcode)], limit=1)
                if not existing_product:
                    existing_product = product_obj.search([('barcode', '=', item.get('code_dimportation', ''))],
                                                          limit=1)
                if not existing_product:
                    existing_product = product_obj.search([('name', '=', item.get('titre', ''))], limit=1)

                if existing_product:
                    _LOGGER.info("🔄 Product already exists, updating...")
                    existing_product.write({
                        'name': item.get('titre', ''),
                        'list_price': price,
                        'type': 'product',
                        'barcode': barcode,
                        'description_sale': item.get('titre', ''),
                        'weight': weight,
                        'sale_ok': True,
                        'purchase_ok': True,
                        'image_1920': image_data
                    })

                    # Si il y a plus d'une image, on les télécharge et on les associe au produit
                    if len(images_url_array) > 1:
                        self.set_images_from_urls(existing_product, images_url_array[1:])
                else:
                    _LOGGER.info("➡️ Product doesn't exist, creating ...")
                    new_product = product_obj.create({
                        'name': item.get('titre', ''),
                        'list_price': price,
                        'type': 'product',
                        'barcode': barcode,
                        'description_sale': item.get('titre', ''),
                        'weight': weight,
                        'sale_ok': True,
                        'purchase_ok': True,
                        'image_1920': image_data
                    })
                    # Si il y a plus d'une image, on les télécharge et on les associe au nouveau produit
                    if len(images_url_array) > 1:
                        self.set_images_from_urls(new_product, images_url_array[1:])
            except Exception as e:
                _LOGGER.error(f"Erreur lors de la création/mise à jour du produit: {e}")
                continue
                # raise UserError(f"Erreur lors de la création/mise à jour du produit '{item.get('titre', '')}': {e}")
        # # Utiliser le modèle `mail.message` pour envoyer une notification utilisateur
        # self.env['mail.message'].create({
        #     'body': "L'importation des produits a été réalisée avec succès. ✅",
        #     'subject': "Succès ! 🎉",
        #     'message_type': 'notification',
        #     'subtype_id': self.env.ref('mail.mt_note').id,
        #     'partner_ids': [(4, self.env.user.partner_id.id)],  # Envoie la notification à l'utilisateur courant
        # })

        _LOGGER.info("✅ Importation des produits terminée avec succès")

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_export_barcode_for_code2asin(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/export/barcodes',
            'target': 'self',
        }
