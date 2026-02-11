# -*- coding: utf-8 -*-
from odoo import models, fields, api


class PlanetePimImportErrorLine(models.Model):
    """Détails des lignes problématiques lors d'un import PIM.
    
    Stocke les informations sur chaque ligne qui n'a pas pu être importée
    normalement, avec la raison précise et les données brutes.
    """
    _name = "planete.pim.import.error.line"
    _description = "Ligne d'erreur/anomalie d'import PIM"
    _order = "row_number"
    
    # Référence à l'historique d'import
    history_id = fields.Many2one(
        'planete.pim.import.history',
        string="Historique d'import",
        required=True,
        ondelete='cascade',
        index=True,
    )
    
    # Informations de base
    row_number = fields.Integer(
        string="Numéro de ligne",
        help="Numéro de ligne dans le fichier CSV (en-tête non comptée)",
    )
    
    ean = fields.Char(
        string="EAN",
        help="Code-barres EAN du produit (brut)",
    )
    
    reference = fields.Char(
        string="Référence",
        help="Référence/SKU du produit",
    )
    
    product_name = fields.Char(
        string="Nom produit",
        help="Nom du produit dans le fichier",
    )
    
    # Type d'erreur/anomalie
    error_type = fields.Selection([
        ('skipped_existing', 'Produit existant (ignoré)'),
        ('no_ean', 'EAN manquant ou invalide'),
        ('duplicate_ean', 'EAN en doublon dans le fichier'),
        ('duplicate_ref', 'Référence en doublon'),
        ('deduped', 'Ligne identique dédupliquée'),
        ('no_brand', 'Marque manquante'),
        ('import_error', 'Erreur technique'),
        ('other', 'Autre'),
    ], string="Type d'anomalie", required=True, index=True)
    
    error_details = fields.Text(
        string="Détails",
        help="Explication détaillée de l'anomalie",
    )
    
    # Données brutes pour analyse
    raw_data = fields.Text(
        string="Données brutes (JSON)",
        help="Données complètes de la ligne CSV (format JSON)",
    )
    
    # Métadonnées
    duplicate_count = fields.Integer(
        string="Nombre d'occurrences",
        help="Pour les doublons : nombre de fois que cette ligne apparaît",
        default=1,
    )
    
    duplicate_rows = fields.Char(
        string="Numéros de lignes en doublon",
        help="Liste des numéros de lignes concernées (ex: '123, 456, 789')",
    )
    
    # Pour les produits existants
    existing_product_id = fields.Many2one(
        'product.template',
        string="Produit existant",
        help="Lien vers le produit qui existe déjà en base",
        ondelete='set null',
    )
    
    # Actions
    action_taken = fields.Selection([
        ('none', 'Aucune'),
        ('quarantine', 'Mis en quarantaine'),
        ('skipped', 'Ignoré'),
        ('corrected', 'Corrigé'),
    ], string="Action", default='none')
    
    def action_view_product(self):
        """Ouvre le produit existant (pour les skipped_existing)."""
        self.ensure_one()
        if not self.existing_product_id:
            return
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': self.existing_product_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
