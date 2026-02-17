from odoo import http
from odoo.http import request
import csv
import io


class ProductExportController(http.Controller):

    @http.route('/web/export/barcodes', type='http', auth="user")
    def export_product_barcodes(self, **kwargs):
        # Récupérer les produits qui ont un barcode
        products = request.env['product.product'].sudo().search([('barcode', '!=', False)])

        # Créer un buffer pour stocker le CSV en mémoire
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)

        # Écrire l'en-tête de colonne
        csv_writer.writerow(['ean'])

        # Écrire chaque barcode dans le CSV
        for product in products:
            csv_writer.writerow([product.barcode])

        # Récupérer le contenu du CSV
        csv_data = csv_buffer.getvalue()

        # Fermer le buffer
        csv_buffer.close()

        # Retourner le fichier CSV pour téléchargement
        return request.make_response(csv_data, headers=[('Content-Type', 'text/csv'),
                                                        ('Content-Disposition', 'attachment; filename="liste-ean.csv";')
                                                        ]
                                     )
