# -*- coding: utf-8 -*-
"""
Planète PIM - Marques en attente de validation

Ce modèle stocke les marques rencontrées lors des imports qui n'existent pas
encore dans la base de données et nécessitent une validation manuelle.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging
_logger = logging.getLogger(__name__)


class PlanetePimBrandPending(models.Model):
    _name = "planete.pim.brand.pending"
    _description = "Marques en attente de validation"
    _order = "create_date desc, name"
    _rec_name = "name"

    name = fields.Char(
        string="Nom dans le fichier",
        required=True,
        help="Nom de la marque tel qu'il apparaît dans le fichier d'import du fournisseur",
    )
    provider_id = fields.Many2one(
        "ftp.provider",
        string="Fournisseur",
        required=True,
        ondelete="cascade",
        help="Fournisseur qui a envoyé cette marque",
    )
    provider_name = fields.Char(
        related="provider_id.name",
        string="Nom fournisseur",
        store=True,
    )
    product_count = fields.Integer(
        string="Produits concernés",
        default=1,
        help="Nombre de produits avec cette marque dans le dernier import",
    )
    import_date = fields.Datetime(
        string="Date d'import",
        default=fields.Datetime.now,
    )
    last_seen_date = fields.Datetime(
        string="Dernière occurrence",
        default=fields.Datetime.now,
    )
    suggested_brand_id = fields.Many2one(
        "product.brand",
        string="Marque suggérée",
        help="Marque existante qui pourrait correspondre (suggestion automatique)",
    )
    validated_brand_id = fields.Many2one(
        "product.brand",
        string="Marque validée",
        help="Marque choisie par l'utilisateur",
    )
    
    # Champ pour créer une nouvelle marque avec un nom personnalisé
    new_brand_name = fields.Char(
        string="Nom de la nouvelle marque",
        help="Si vous voulez créer une marque avec un nom différent du fichier, "
             "saisissez-le ici (ex: 'Magarantie.com' au lieu de 'MGC'). "
             "Le nom du fichier sera automatiquement ajouté comme alias.",
    )
    
    state = fields.Selection([
        ("pending", "En attente"),
        ("validated", "Validée"),
        ("ignored", "Ignorée"),
        ("new_brand", "Nouvelle marque créée"),
    ], string="État", default="pending", required=True)
    
    notes = fields.Text(
        string="Notes",
        help="Notes ou commentaires sur cette marque",
    )
    
    # Champs pour le tracking
    create_uid = fields.Many2one("res.users", string="Créé par", readonly=True)
    write_uid = fields.Many2one("res.users", string="Modifié par", readonly=True)
    
    _sql_constraints = [
        ('unique_name_provider', 'UNIQUE(name, provider_id)',
         'Cette marque existe déjà pour ce fournisseur'),
    ]

    # =========================================================================
    # Actions
    # =========================================================================
    
    def action_validate_with_existing(self):
        """Valide en associant à une marque existante et ajoute l'alias.
        
        ✅ AMÉLIORÉ: Résout aussi le même nom de marque chez TOUS les autres fournisseurs.
        """
        self.ensure_one()
        if not self.validated_brand_id:
            raise UserError(_("Veuillez sélectionner une marque existante."))
        
        # Ajouter l'alias à la marque
        brand = self.validated_brand_id
        current_aliases = brand.aliases or ""
        alias_list = [a.strip().upper() for a in current_aliases.split(",") if a.strip()]
        new_alias = self.name.strip().upper()
        
        if new_alias not in alias_list:
            alias_list.append(new_alias)
            brand.write({"aliases": ",".join(alias_list)})
            _logger.info("Added alias '%s' to brand '%s' (id=%d)", self.name, brand.name, brand.id)
        
        self.write({
            "state": "validated",
        })
        
        # ✅ CROSS-PROVIDER CLEANUP: Résoudre la même marque chez tous les autres fournisseurs
        resolved_count = self._resolve_same_brand_all_providers(self.name, brand)
        
        msg = _("'%s' est maintenant un alias de '%s'") % (self.name, brand.name)
        if resolved_count > 0:
            msg += _(" (+%d autres fournisseurs résolus)") % resolved_count
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Marque validée"),
                'message': msg,
                'type': 'success',
                'sticky': False,
            }
        }

    def action_create_new_brand(self):
        """Crée une nouvelle marque avec ce nom ou un nom personnalisé.
        
        Si new_brand_name est rempli, crée la marque avec ce nom et ajoute
        le nom du fichier (self.name) comme alias.
        Sinon, crée la marque avec le nom du fichier (self.name).
        """
        self.ensure_one()
        
        Brand = self.env["product.brand"].sudo()
        
        # Déterminer le nom à utiliser
        brand_name = (self.new_brand_name or "").strip() or self.name.strip()
        file_name = self.name.strip()
        
        # Vérifier que la marque n'existe pas déjà
        existing = Brand.search([("name", "=ilike", brand_name)], limit=1)
        if existing:
            raise UserError(_("Une marque avec ce nom existe déjà: %s") % existing.name)
        
        # Préparer les valeurs de création
        vals = {"name": brand_name}
        
        # Si le nom est différent du fichier, ajouter le nom du fichier comme alias
        if brand_name.upper() != file_name.upper():
            vals["aliases"] = file_name.upper()
            _logger.info("Creating brand '%s' with alias '%s' from file", brand_name, file_name)
        
        # Créer la marque
        new_brand = Brand.create(vals)
        
        self.write({
            "validated_brand_id": new_brand.id,
            "state": "new_brand",
        })
        
        _logger.info("Created new brand '%s' (id=%d) from pending", brand_name, new_brand.id)
        
        # Message de notification
        if brand_name.upper() != file_name.upper():
            message = _("La marque '%s' a été créée avec '%s' comme alias.") % (brand_name, file_name)
        else:
            message = _("La marque '%s' a été créée.") % brand_name
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Marque créée"),
                'message': message,
                'type': 'success',
                'sticky': False,
            }
        }

    def action_ignore(self):
        """Ignore cette marque (ne sera plus notifiée)."""
        self.ensure_one()
        self.write({"state": "ignored"})
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Marque ignorée"),
                'message': _("La marque '%s' a été ignorée.") % self.name,
                'type': 'warning',
                'sticky': False,
            }
        }

    def action_reset_to_pending(self):
        """Remet en attente pour re-traitement."""
        self.write({"state": "pending", "validated_brand_id": False})

    # =========================================================================
    # Méthodes utilitaires
    # =========================================================================
    
    @api.model
    def upsert_pending_brand(self, brand_name, provider_id, product_count=1):
        """Crée ou met à jour une marque en attente.
        
        Args:
            brand_name: Nom de la marque dans le fichier
            provider_id: ID du provider (ftp.provider)
            product_count: Nombre de produits concernés
            
        Returns:
            planete.pim.brand.pending record
        """
        if not brand_name or not provider_id:
            return self.browse()
        
        brand_name_clean = brand_name.strip()
        
        # Chercher si cette marque existe déjà pour ce provider
        existing = self.search([
            ("name", "=ilike", brand_name_clean),
            ("provider_id", "=", provider_id),
        ], limit=1)
        
        if existing:
            # Mettre à jour
            existing.write({
                "product_count": existing.product_count + product_count,
                "last_seen_date": fields.Datetime.now(),
            })
            return existing
        else:
            # Créer
            # Chercher une suggestion automatique
            suggested = self._find_suggested_brand(brand_name_clean)
            
            return self.create({
                "name": brand_name_clean,
                "provider_id": provider_id,
                "product_count": product_count,
                "suggested_brand_id": suggested.id if suggested else False,
            })

    @api.model
    def _find_suggested_brand(self, brand_name):
        """Cherche une marque existante qui pourrait correspondre.
        
        Utilise une recherche partielle (LIKE) pour suggérer des correspondances.
        """
        if not brand_name:
            return self.env["product.brand"].browse()
        
        Brand = self.env["product.brand"].sudo()
        brand_name_clean = brand_name.strip().upper()
        
        # 1. Recherche exacte
        exact = Brand.search([("name", "=ilike", brand_name_clean)], limit=1)
        if exact:
            return exact
        
        # 2. Recherche dans les aliases
        all_brands = Brand.search([("aliases", "!=", False)])
        for brand in all_brands:
            if brand.aliases:
                alias_list = [a.strip().upper() for a in brand.aliases.split(",") if a.strip()]
                if brand_name_clean in alias_list:
                    return brand
        
        # 3. Recherche partielle (LIKE)
        if len(brand_name_clean) >= 3:
            partial = Brand.search([
                "|",
                ("name", "ilike", brand_name_clean[:3]),
                ("aliases", "ilike", brand_name_clean[:3]),
            ], limit=5)
            if len(partial) == 1:
                return partial
        
        return self.env["product.brand"].browse()

    @api.model
    def get_pending_count_by_provider(self):
        """Retourne le nombre de marques en attente par provider."""
        result = {}
        pending = self.search([("state", "=", "pending")])
        for rec in pending:
            provider_id = rec.provider_id.id
            if provider_id not in result:
                result[provider_id] = {"name": rec.provider_name, "count": 0}
            result[provider_id]["count"] += 1
        return result

    # =========================================================================
    # Cross-provider cleanup & Re-verify
    # =========================================================================

    def _resolve_same_brand_all_providers(self, brand_file_name, brand_record):
        """Résout la même marque chez TOUS les autres fournisseurs.
        
        Quand on valide 'LINDY' -> brand Lindy pour le provider A,
        cette méthode résout automatiquement tous les pending 'LINDY'
        chez les providers B, C, D etc.
        
        Returns:
            int: Nombre d'enregistrements résolus chez d'autres providers
        """
        if not brand_file_name or not brand_record:
            return 0
        
        others = self.search([
            ("name", "=ilike", brand_file_name.strip()),
            ("state", "=", "pending"),
            ("id", "!=", self.id if self else 0),
        ])
        
        count = 0
        for rec in others:
            rec.write({
                "validated_brand_id": brand_record.id,
                "state": "validated",
            })
            count += 1
            _logger.info("Auto-resolved brand '%s' for provider %s -> %s (id=%d)",
                        brand_file_name, rec.provider_name, brand_record.name, brand_record.id)
        
        if count:
            _logger.info("Cross-provider cleanup: resolved '%s' for %d other providers", brand_file_name, count)
        return count

    @api.model
    def action_reverify_all_pending(self):
        """Re-vérifie TOUTES les marques en attente contre les marques existantes + aliases.
        
        Cas d'usage:
        - Après ajout d'aliases sur des marques existantes
        - Après création de nouvelles marques
        - Pour nettoyer les marques comme LINDY, ACER qui existent déjà
        
        LOGIQUE:
        1. Pour chaque marque pending, chercher dans product.brand par nom exact
        2. Chercher dans les aliases
        3. Chercher avec nettoyage Unicode (TRIM, accents, invisible chars)
        4. Si trouvée -> auto-valider + ajouter l'alias
        
        Returns:
            Action notification avec le nombre de marques résolues
        """
        import unicodedata
        
        Brand = self.env["product.brand"].sudo()
        pending_records = self.search([("state", "=", "pending")])
        
        if not pending_records:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Re-vérification"),
                    'message': _("Aucune marque en attente à vérifier."),
                    'type': 'info',
                    'sticky': False,
                }
            }
        
        # Charger toutes les marques une seule fois
        all_brands = Brand.search([])
        brand_by_name = {}   # lower(clean(name)) -> brand
        brand_by_alias = {}  # lower(clean(alias)) -> brand
        
        def _clean(s):
            """Nettoie: strip, accents, invisible chars, lowercase."""
            if not s:
                return ""
            s = str(s).strip()
            # Supprimer les chars invisibles
            for ch in '\u200b\u200c\u200d\ufeff\u00a0\u2007\u202f\u2060\u180e':
                s = s.replace(ch, '')
            # Normaliser les accents
            s = unicodedata.normalize("NFKD", s)
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
            return s.lower().strip()
        
        for brand in all_brands:
            key = _clean(brand.name)
            if key:
                brand_by_name[key] = brand
            if brand.aliases:
                for alias in brand.aliases.split(","):
                    akey = _clean(alias)
                    if akey:
                        brand_by_alias[akey] = brand
        
        resolved = 0
        for rec in pending_records:
            pending_key = _clean(rec.name)
            if not pending_key:
                continue
            
            # 1. Nom exact
            matched_brand = brand_by_name.get(pending_key)
            
            # 2. Alias exact
            if not matched_brand:
                matched_brand = brand_by_alias.get(pending_key)
            
            if matched_brand:
                # Auto-valider
                rec.write({
                    "validated_brand_id": matched_brand.id,
                    "state": "validated",
                })
                
                # Ajouter l'alias si pas déjà présent
                current_aliases = matched_brand.aliases or ""
                alias_list = [a.strip().upper() for a in current_aliases.split(",") if a.strip()]
                new_alias = rec.name.strip().upper()
                if new_alias not in alias_list and new_alias.upper() != matched_brand.name.strip().upper():
                    alias_list.append(new_alias)
                    matched_brand.write({"aliases": ",".join(alias_list)})
                
                resolved += 1
                _logger.info("Re-verify: '%s' (provider=%s) -> matched brand '%s' (id=%d)",
                            rec.name, rec.provider_name, matched_brand.name, matched_brand.id)
        
        _logger.info("Re-verify completed: %d/%d pending brands resolved", resolved, len(pending_records))
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Re-vérification terminée"),
                'message': _("%d marque(s) résolue(s) sur %d en attente") % (resolved, len(pending_records)),
                'type': 'success' if resolved > 0 else 'info',
                'sticky': True,
            }
        }
