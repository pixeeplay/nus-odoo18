# -*- coding: utf-8 -*-
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PlanetePimStagingDeleteWizard(models.TransientModel):
    _name = "planete.pim.staging.delete.wizard"
    _description = "Assistant de suppression massive de la quarantaine"

    total_count = fields.Integer(
        string="Produits en quarantaine",
        readonly=True,
        help="Nombre total de produits en quarantaine à supprimer",
    )
    
    def action_confirm_delete(self):
        """Supprime TOUS les produits en quarantaine par batch SQL direct.
        
        Utilise des requêtes SQL DELETE par batch de 50,000 pour contourner
        la limitation Odoo de 20,000 enregistrements.
        """
        self.ensure_one()
        
        if self.total_count == 0:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Quarantaine vide"),
                    "message": _("Aucun produit en quarantaine à supprimer."),
                    "type": "info",
                    "sticky": False,
                }
            }
        
        try:
            _logger.info("[QUARANTINE] Starting massive delete of %d records", self.total_count)
            
            # Suppression par batch SQL direct pour éviter la limite de 20k
            batch_size = 50000
            deleted_total = 0
            
            while True:
                # Supprimer par batch de 50k avec CTID pour performance
                self.env.cr.execute("""
                    DELETE FROM planete_pim_product_staging
                    WHERE ctid IN (
                        SELECT ctid FROM planete_pim_product_staging
                        LIMIT %s
                    )
                """, [batch_size])
                
                deleted_count = self.env.cr.rowcount
                deleted_total += deleted_count
                
                _logger.info("[QUARANTINE] Deleted batch: %d records (total: %d/%d)", 
                            deleted_count, deleted_total, self.total_count)
                
                # Commit après chaque batch pour libérer la mémoire
                self.env.cr.commit()
                
                # Si moins de records supprimés que le batch_size, c'est fini
                if deleted_count < batch_size:
                    break
            
            _logger.info("[QUARANTINE] Massive delete completed: %d records deleted", deleted_total)
            
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Suppression réussie"),
                    "message": _("%d produits supprimés de la quarantaine.") % deleted_total,
                    "type": "success",
                    "sticky": False,
                    "next": {
                        "type": "ir.actions.act_window_close",
                    }
                }
            }
            
        except Exception as e:
            _logger.exception("[QUARANTINE] Error during massive delete: %s", e)
            raise UserError(_("Erreur lors de la suppression massive: %s") % str(e))
