# -*- coding: utf-8 -*-
from . import models
from . import wizard
from . import controllers

import os
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """
    Seed ftp.provider records from a local file after module install/upgrade.

    Priority of path:
      1) Environment variable IVSPRO_FTP_PROVIDERS_PATH
      2) ir.config_parameter 'ftp_tariff_import.providers_path'

    Supported formats are handled by ftp.provider.seed_from_path().
    """
    try:
        path = os.environ.get("IVSPRO_FTP_PROVIDERS_PATH")
        if not path:
            path = (env["ir.config_parameter"].sudo()
                    .get_param("ftp_tariff_import.providers_path") or "")
        if not path:
            base_dir = os.path.dirname(__file__)
            # Try module-local fallback files
            local_candidates = [
                os.path.join(base_dir, "data", "stockage_data.json"),
                os.path.join(base_dir, "data", "stockage_data"),
            ]
            for lp in local_candidates:
                if os.path.exists(lp):
                    path = lp
                    _logger.info("ftp_tariff_import: using local providers seed file: %s", path)
                    break
        if not path:
            _logger.info("ftp_tariff_import: no providers seed path configured or local fallback; skipping.")
            return
        env["ftp.provider"].sudo().seed_from_path(path)
    except Exception:
        _logger.exception("ftp_tariff_import: post_init_hook failed")
    
    # Rafraîchir le registre des champs disponibles pour le mapping
    # Cela garantit que tous les champs (product.template, product.odr, etc.) sont disponibles
    try:
        FieldRegistry = env["ftp.mapping.field.registry"].sudo()
        FieldRegistry._refresh_product_template_fields()
        _logger.info("ftp_tariff_import: field registry refreshed successfully")
    except Exception as e:
        _logger.warning("ftp_tariff_import: could not refresh field registry: %s", e)
