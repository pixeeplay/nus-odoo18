# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PrestaShopImportWizard(models.TransientModel):
    _name = 'prestashop.import.wizard'
    _description = 'Import Selected PrestaShop Orders'

    preview_ids = fields.Many2many('prestashop.order.preview', string='Orders to Import')
    import_count = fields.Integer(compute='_compute_import_count')

    @api.depends('preview_ids')
    def _compute_import_count(self):
        for wizard in self:
            wizard.import_count = len(wizard.preview_ids)

    def action_import_selected(self):
        """Import all selected orders"""
        if not self.preview_ids:
            raise UserError(_("No orders selected for import!"))

        success_count = 0
        error_count = 0
        errors = []

        for preview in self.preview_ids:
            if preview.state == 'imported':
                continue

            try:
                preview._import_to_odoo()
                success_count += 1
            except Exception as e:
                error_count += 1
                errors.append(f"{preview.name}: {str(e)}")

        message = _("Import complete: %d success, %d errors") % (success_count, error_count)
        if errors:
            message += "\n\nErrors:\n" + "\n".join(errors)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Complete'),
                'message': message,
                'type': 'success' if error_count == 0 else 'warning',
                'sticky': True,
            }
        }
