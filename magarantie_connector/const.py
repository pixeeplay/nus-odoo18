# -*- coding: utf-8 -*-

# MaGarantie API base URL
API_BASE_URL = 'https://pro.magarantie.com/api-webservice/api/v1'

# API Endpoints (all use POST with 'token' parameter)
ENDPOINTS = {
    'get_categories': '/get-categories.php',
    'get_produits': '/get-produits.php',
    'get_garantie': '/get-garantie.php',
    'get_infos_garantie': '/get-infos-garantie.php',
    'post_vente': '/post-vente.php',
    'post_date_mise_en_service': '/post-date-mise-en-service.php',
    'get_vente': '/get-vente.php',
    'get_ventes': '/get-ventes.php',
    'get_ventes_programme': '/get-ventes-programme.php',
    'get_certificat_garantie': '/get-certificat-garantie.php',
    'get_notice_utilisation': '/get-notice-utilisation.php',
    'get_ipid': '/get-ipid.php',
    'get_programmes': '/get-programmes.php',
    'get_programme_garanties': '/get-programme-garanties.php',
    'get_all_garanties': '/get-all-garanties.php',
    'get_all_garanties_by_programme': '/get-all-garanties-by-programme.php',
}
