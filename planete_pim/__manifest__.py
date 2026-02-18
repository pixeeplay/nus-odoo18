# -*- coding: utf-8 -*-
{
    "name": "Product Information Management (PIM)",
    "summary": "Centralized product management with supplier planning, staging import and brand management",
    "description": """
Product Information Management (PIM)
=====================================
Complete PIM solution for Odoo with supplier overview, product planning and import workflows.

Features:
---------
* Kanban overview of suppliers and product planning
* Staging area for product imports with validation
* File import wizard (CSV/Excel) with column mapping
* Brand management with alias history
* Planification board for product lifecycle
* Job queue for background processing
* Colbee integration for product data
* Multi-company and multi-brand support
    """,
    "version": "19.0.2.11.0",
    "author": "Antigravity",
    "website": "https://antigravity.fr",
    "support": "support@antigravity.fr",
    "license": "OPL-1",
    "price": 199,
    "currency": "EUR",
    "category": "Sales/Products",
    "depends": [
        "base",
        "product",
        "stock",
        "purchase",
        "product_brand",
        "ftp_tariff_import",
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/overview_views.xml",
        "views/file_import_wizard_views.xml",
        "views/staging_views.xml",
        "views/planification_views.xml",
        "views/provider_inherit.xml",
        "views/job_views.xml",
        "views/brand_pending_views.xml",
        "views/brand_alias_history_views.xml",
        "views/colbee_views.xml",
        "views/product_views.xml",
        "views/menuitems.xml",
        "views/planning_views.xml",
        "wizards/staging_delete_wizard_view.xml",
        "data/planete_pim_cron.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
    "auto_install": False,
}
