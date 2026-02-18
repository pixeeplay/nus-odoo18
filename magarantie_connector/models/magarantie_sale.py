# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MaGarantieSale(models.Model):
    _name = 'magarantie.sale'
    _description = 'MaGarantie Warranty Sale'
    _order = 'create_date desc'

    idvente = fields.Char(
        string='Sale ID (API)',
        index=True,
        readonly=True,
        copy=False,
        help="ID returned by MaGarantie after post-vente",
    )

    # Links to Odoo records
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        ondelete='set null',
    )
    sale_order_line_id = fields.Many2one(
        'sale.order.line',
        string='Sale Order Line',
        ondelete='set null',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
    )
    product_template_id = fields.Many2one(
        'product.template',
        string='Product',
    )
    warranty_id = fields.Many2one(
        'magarantie.warranty',
        string='Warranty Offer',
    )

    # Customer info sent to API
    nom = fields.Char(string='Last Name')
    prenom = fields.Char(string='First Name')
    email = fields.Char(string='Email')
    telephone = fields.Char(string='Phone')
    adresse = fields.Char(string='Address')
    adresse2 = fields.Char(string='Address 2')
    code_postal = fields.Char(string='Postal Code')
    ville = fields.Char(string='City')

    # Product/warranty info sent to API
    idgarantie = fields.Char(string='Warranty ID')
    garantie_prix = fields.Float(string='Warranty Price', digits=(12, 2))
    rubrique = fields.Char(string='Rubrique')
    produit_prix = fields.Float(string='Product Price', digits=(12, 2))
    date_achat = fields.Date(string='Purchase Date')
    produit_marque = fields.Char(string='Brand')
    produit_modele = fields.Char(string='Model')
    produit_num_serie = fields.Char(string='Serial Number')
    custom = fields.Char(string='Custom Field')
    date_mise_en_service = fields.Date(string='Commissioning Date')

    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('confirmed', 'Confirmed'),
        ('error', 'Error'),
    ], string='Status', default='draft', readonly=True, copy=False)
    api_response = fields.Text(string='API Response', readonly=True)
    error_message = fields.Text(string='Error Message', readonly=True)

    # PDF documents (base64)
    certificat_garantie = fields.Binary(
        string='Warranty Certificate',
        readonly=True,
    )
    certificat_garantie_filename = fields.Char(
        compute='_compute_filenames',
    )
    notice_utilisation = fields.Binary(
        string='Usage Notice',
        readonly=True,
    )
    notice_utilisation_filename = fields.Char(
        compute='_compute_filenames',
    )
    ipid_document = fields.Binary(
        string='IPID Document',
        readonly=True,
    )
    ipid_document_filename = fields.Char(
        compute='_compute_filenames',
    )

    @api.depends('idvente')
    def _compute_filenames(self):
        for rec in self:
            suffix = rec.idvente or 'new'
            rec.certificat_garantie_filename = "certificat_%s.pdf" % suffix
            rec.notice_utilisation_filename = "notice_%s.pdf" % suffix
            rec.ipid_document_filename = "ipid_%s.pdf" % suffix

    @api.depends('idvente', 'nom', 'prenom')
    def _compute_display_name(self):
        for rec in self:
            parts = []
            if rec.idvente:
                parts.append("#%s" % rec.idvente)
            name = ("%s %s" % (rec.prenom or '', rec.nom or '')).strip()
            if name:
                parts.append(name)
            rec.display_name = ' - '.join(parts) or _('New')

    def action_send_to_api(self):
        """Submit this warranty sale to the MaGarantie API."""
        self.ensure_one()
        if self.state in ('sent', 'confirmed'):
            raise UserError(_("This sale has already been sent to MaGarantie."))

        api_client = self.env['res.config.settings']._get_magarantie_api()

        vente_data = {
            'nom': self.nom or '',
            'prenom': self.prenom or '',
            'adresse': self.adresse or '',
            'codepostal': self.code_postal or '',
            'ville': self.ville or '',
            'email': self.email or '',
            'telephone': self.telephone or '',
            'idgarantie': self.idgarantie or '',
            'garantie_prix': str(self.garantie_prix),
            'date_achat': self.date_achat.strftime('%Y-%m-%d') if self.date_achat else '',
            'produit_marque': self.produit_marque or '',
            'produit_modele': self.produit_modele or '',
            'produit_prix': str(self.produit_prix),
            'custom': self.custom or '',
        }
        if self.adresse2:
            vente_data['adresse2'] = self.adresse2
        if self.produit_num_serie:
            vente_data['produit_num_serie'] = self.produit_num_serie

        try:
            result = api_client.post_vente(vente_data)
            idvente = ''
            if isinstance(result, dict):
                idvente = str(result.get('idvente', ''))
            self.write({
                'state': 'confirmed' if idvente else 'error',
                'idvente': idvente,
                'api_response': str(result),
                'error_message': False,
            })
            _logger.info("MaGarantie sale posted: idvente=%s", idvente)
        except UserError as e:
            self.write({
                'state': 'error',
                'error_message': str(e),
            })
            raise

    def action_send_commissioning_date(self):
        """Send commissioning date to MaGarantie."""
        self.ensure_one()
        if not self.idvente:
            raise UserError(_("No MaGarantie sale ID. Send the sale first."))
        if not self.date_mise_en_service:
            raise UserError(_("Please set the commissioning date first."))

        api_client = self.env['res.config.settings']._get_magarantie_api()
        result = api_client.post_date_mise_en_service(
            self.idvente,
            self.date_mise_en_service.strftime('%Y-%m-%d'),
        )
        self.api_response = str(result)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Commissioning date sent to MaGarantie.'),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_fetch_documents(self):
        """Download all available PDF documents from MaGarantie."""
        self.ensure_one()
        if not self.idvente:
            raise UserError(_("No MaGarantie sale ID."))

        api_client = self.env['res.config.settings']._get_magarantie_api()
        vals = {}

        # Warranty certificate
        try:
            cert = api_client.get_certificat_garantie(self.idvente)
            if cert and isinstance(cert, dict) and cert.get('fichier_pdf'):
                vals['certificat_garantie'] = cert['fichier_pdf']
        except Exception as e:
            _logger.warning(
                "Could not fetch certificate for %s: %s", self.idvente, e
            )

        # Usage notice
        try:
            notice = api_client.get_notice_utilisation(self.idvente)
            if notice and isinstance(notice, dict) and notice.get('fichier_pdf'):
                vals['notice_utilisation'] = notice['fichier_pdf']
        except Exception as e:
            _logger.warning(
                "Could not fetch notice for %s: %s", self.idvente, e
            )

        # IPID
        try:
            ipid = api_client.get_ipid(self.idvente)
            if ipid and isinstance(ipid, dict) and ipid.get('fichier_pdf'):
                vals['ipid_document'] = ipid['fichier_pdf']
        except Exception as e:
            _logger.warning(
                "Could not fetch IPID for %s: %s", self.idvente, e
            )

        if vals:
            self.write(vals)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Documents Downloaded'),
                    'message': _('%d document(s) downloaded.') % len(vals),
                    'type': 'success',
                    'sticky': False,
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('No Documents'),
                'message': _('No documents available yet.'),
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_refresh_status(self):
        """Refresh sale status from API."""
        self.ensure_one()
        if not self.idvente:
            raise UserError(_("No MaGarantie sale ID."))
        api_client = self.env['res.config.settings']._get_magarantie_api()
        result = api_client.get_vente(self.idvente)
        if result:
            self.api_response = str(result)
