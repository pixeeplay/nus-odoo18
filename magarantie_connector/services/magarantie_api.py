# -*- coding: utf-8 -*-
import logging
import requests
from odoo.exceptions import UserError
from odoo import _
from odoo.addons.magarantie_connector import const

_logger = logging.getLogger(__name__)


class MaGarantieAPI:
    """Service class for MaGarantie.com API communication.

    All endpoints use POST with a 'token' parameter.
    Responses are JSON.
    """

    def __init__(self, token, timeout=30):
        self.token = token
        self.base_url = const.API_BASE_URL
        self.timeout = timeout

    def _call(self, endpoint_key, params=None):
        """Generic POST call to MaGarantie API.

        :param endpoint_key: Key in const.ENDPOINTS dict
        :param params: dict of additional POST parameters
        :return: parsed JSON response
        :raises UserError: on HTTP or parse errors
        """
        url = self.base_url + const.ENDPOINTS[endpoint_key]
        data = {'token': self.token}
        if params:
            data.update(params)

        _logger.info(
            "MaGarantie API call: %s with params: %s",
            url,
            {k: v for k, v in data.items() if k != 'token'},
        )
        try:
            response = requests.post(url, data=data, timeout=self.timeout)
            _logger.info("MaGarantie API response: status %s", response.status_code)

            if response.status_code != 200:
                _logger.error(
                    "MaGarantie API error %s: %s",
                    response.status_code,
                    response.text[:500],
                )
                raise UserError(
                    _("MaGarantie API error (status %s)") % response.status_code
                )

            result = response.json()

            # Check for error in JSON body
            if isinstance(result, dict) and result.get('error'):
                error_msg = result.get('error', '')
                error_code = result.get('code', '')
                raise UserError(
                    _("MaGarantie API error [%s]: %s") % (error_code, error_msg)
                )

            return result

        except requests.exceptions.RequestException as e:
            _logger.error("MaGarantie connection error: %s", str(e))
            raise UserError(_("MaGarantie connection error: %s") % str(e))
        except ValueError:
            _logger.error(
                "MaGarantie invalid JSON response: %s", response.text[:500]
            )
            raise UserError(_("Invalid response from MaGarantie (not JSON)"))

    # --- Category endpoints ---

    def get_categories(self):
        """Get list of categories (rubriques)."""
        return self._call('get_categories')

    def get_produits(self, rubrique):
        """Get insurable products by category."""
        return self._call('get_produits', {'rubrique': rubrique})

    # --- Warranty endpoints ---

    def get_garantie(self, rubrique, date_achat, prix_achat):
        """Get warranty ID matching rubrique + purchase date + purchase price."""
        return self._call('get_garantie', {
            'rubrique': rubrique,
            'date_achat': date_achat,
            'prix_achat': str(prix_achat),
        })

    def get_infos_garantie(self, idgarantie):
        """Get warranty details by ID."""
        return self._call('get_infos_garantie', {'idgarantie': str(idgarantie)})

    def get_all_garanties(self):
        """Get all warranty extensions (default programme)."""
        return self._call('get_all_garanties')

    def get_all_garanties_by_programme(self, programme):
        """Get warranty extensions for a specific programme."""
        return self._call('get_all_garanties_by_programme', {
            'programme': programme,
        })

    def get_programmes(self):
        """Get available programmes."""
        return self._call('get_programmes')

    def get_programme_garanties(self, programme):
        """Get warranties for a specific programme."""
        return self._call('get_programme_garanties', {'programme': programme})

    # --- Sale endpoints ---

    def post_vente(self, vente_data):
        """Submit a warranty sale. vente_data is a dict with all required fields."""
        return self._call('post_vente', vente_data)

    def post_date_mise_en_service(self, idvente, datemes):
        """Submit commissioning date for a kitchen sale."""
        return self._call('post_date_mise_en_service', {
            'idvente': str(idvente),
            'datemes': datemes,
        })

    def get_vente(self, idvente):
        """Get sale info by ID."""
        return self._call('get_vente', {'idvente': str(idvente)})

    def get_ventes(self, start=0, nb=25):
        """Get recent sales."""
        return self._call('get_ventes', {'start': str(start), 'nb': str(nb)})

    def get_ventes_programme(self, start=0, nb=25):
        """Get sales across all programmes."""
        return self._call('get_ventes_programme', {
            'start': str(start),
            'nb': str(nb),
        })

    # --- Document endpoints ---

    def get_certificat_garantie(self, idvente):
        """Get warranty certificate PDF (base64 encoded)."""
        return self._call('get_certificat_garantie', {'idvente': str(idvente)})

    def get_notice_utilisation(self, idvente):
        """Get usage notice PDF (base64 encoded)."""
        return self._call('get_notice_utilisation', {'idvente': str(idvente)})

    def get_ipid(self, idvente):
        """Get IPID document PDF (base64 encoded)."""
        return self._call('get_ipid', {'idvente': str(idvente)})
