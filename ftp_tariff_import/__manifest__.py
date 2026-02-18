# -*- coding: utf-8 -*-
{
    "name": "FTP/SFTP/IMAP Tariff Import",
    "summary": "Import product prices from CSV via FTP/SFTP/IMAP with preview, mapping and scheduling",
    "description": """
FTP/SFTP/IMAP Tariff Import
============================
Automate product price list imports from remote servers and email attachments.

Features:
---------
* FTP, SFTP and IMAP connection support
* Google Drive integration for file browsing
* CSV column mapping with templates
* Manual preview before import
* Scheduled automatic imports via cron
* Detailed import logs and error tracking
* Multi-company support with security rules
* Pre-configured provider templates
    """,
    "version": "18.0.3.0.0",
    "category": "Sales/Products",
    "author": "Antigravity",
    "website": "https://antigravity.fr",
    "support": "support@antigravity.fr",
    "license": "OPL-1",
    "price": 99,
    "currency": "EUR",
    "depends": ["base", "product", "account"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "views/provider_views.xml",
        "views/mapping_import_wizard_views.xml",
        "views/mapping_template_views.xml",
        "views/tariff_import_log_views.xml",
        "views/preview_wizard_views.xml",
        "views/import_wizard_views.xml",
        "views/mapping_wizard_views.xml",
        "views/mapping_config_wizard_views.xml",
        "views/gdrive_browser_wizard_views.xml",
        "views/menuitems.xml",
        "data/sequence.xml",
        "data/ir_cron.xml",
        "data/ftp_providers_preconfigured.xml"
    ],
    "images": ["static/description/banner.png"],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
