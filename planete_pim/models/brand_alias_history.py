# -*- coding: utf-8 -*-
"""
Planète PIM - Historique des alias de marque

Ce modèle trace tous les alias de marque créés automatiquement ou manuellement
pour permettre un audit et une gestion des correspondances de noms de marques.
"""
from odoo import api, fields, models, _

import logging
_logger = logging.getLogger(__name__)


class PlanetePimBrandAliasHistory(models.Model):
    _name = "planete.pim.brand.alias.history"
    _description = "Historique des alias de marque"
    _order = "create_date desc"
    _rec_name = "alias_name"

    brand_id = fields.Many2one(
        "product.brand",
        string="Marque",
        required=True,
        ondelete="cascade",
        help="Marque principale à laquelle l'alias a été ajouté",
    )
    brand_name = fields.Char(
        related="brand_id.name",
        string="Nom de la marque",
        store=True,
        readonly=True,
    )
    alias_name = fields.Char(
        string="Alias créé",
        required=True,
        help="Nom de l'alias qui a été ajouté à la marque",
    )
    provider_id = fields.Many2one(
        "ftp.provider",
        string="Fournisseur",
        ondelete="set null",
        help="Fournisseur qui a déclenché la création de cet alias",
    )
    provider_name = fields.Char(
        related="provider_id.name",
        string="Nom fournisseur",
        store=True,
        readonly=True,
    )
    create_date = fields.Datetime(
        string="Date de création",
        readonly=True,
    )
    create_uid = fields.Many2one(
        "res.users",
        string="Créé par",
        readonly=True,
    )
    auto_created = fields.Boolean(
        string="Création automatique",
        default=True,
        help="True si l'alias a été créé automatiquement par le système, False si créé manuellement",
    )
    source_ean = fields.Char(
        string="EAN source",
        help="EAN du produit qui a déclenché la création de l'alias",
    )
    notes = fields.Text(
        string="Notes",
        help="Informations complémentaires sur cet alias",
    )
