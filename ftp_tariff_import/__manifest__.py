# -*- coding: utf-8 -*-
{
    "name": "FTP/SFTP/IMAP Tariff Import",
    "summary": "Import product sale prices (list_price) from CSV files stored on FTP/SFTP or email attachments via IMAP, with manual preview, local download and scheduled runs",
    "version": "18.0.3.0.0",
    "category": "Sales/Products",
    "author": "Doscaal",
    "website": "https://github.com/Doscaal/ivspro",
    "license": "LGPL-3",
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
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False
}
