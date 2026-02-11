# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class GDriveBrowserWizard(models.TransientModel):
    """Wizard pour naviguer dans les dossiers Google Drive."""
    _name = "ftp.gdrive.browser.wizard"
    _description = "Navigateur Google Drive"

    provider_id = fields.Many2one(
        "ftp.provider",
        string="Provider",
        required=True,
    )
    current_folder_id = fields.Char(
        string="Dossier courant",
        default="root",
    )
    current_folder_name = fields.Char(
        string="Nom du dossier courant",
        compute="_compute_current_folder_name",
    )
    breadcrumb = fields.Char(
        string="Chemin",
        compute="_compute_breadcrumb",
    )
    breadcrumb_html = fields.Html(
        string="Navigation",
        compute="_compute_breadcrumb",
    )

    # Contenu du dossier courant
    item_ids = fields.One2many(
        "ftp.gdrive.browser.wizard.item",
        "wizard_id",
        string="Contenu",
    )

    # Dossier sélectionné pour assignment
    target_field = fields.Selection([
        ('import', 'Dossier d\'import (gdrive_folder_id)'),
        ('export', 'Dossier d\'export (gdrive_export_folder_id)'),
    ], default='import', string="Champ cible")

    @api.depends('current_folder_id')
    def _compute_current_folder_name(self):
        for wizard in self:
            if not wizard.current_folder_id or wizard.current_folder_id == "root":
                wizard.current_folder_name = "Mon Drive"
            else:
                # On utilise le premier item du breadcrumb ou on requête
                wizard.current_folder_name = wizard.current_folder_id

    @api.depends('current_folder_id', 'provider_id')
    def _compute_breadcrumb(self):
        from ..models.backend import get_backend
        for wizard in self:
            if not wizard.provider_id:
                wizard.breadcrumb = "/"
                wizard.breadcrumb_html = "<span>/</span>"
                continue

            try:
                with get_backend(wizard.provider_id, self.env) as backend:
                    path = backend.get_folder_path(wizard.current_folder_id)

                    # Breadcrumb texte
                    wizard.breadcrumb = " > ".join([p.get("name", "") for p in path])

                    # Breadcrumb HTML cliquable
                    parts = []
                    for p in path:
                        folder_id = p.get("id", "root")
                        name = p.get("name", "?")
                        parts.append(f'<a href="#" class="gdrive-breadcrumb" data-folder-id="{folder_id}">{name}</a>')
                    wizard.breadcrumb_html = " &gt; ".join(parts)
            except Exception as e:
                wizard.breadcrumb = _("Erreur: %s") % str(e)
                wizard.breadcrumb_html = wizard.breadcrumb

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        provider_id = self.env.context.get('default_provider_id')
        if provider_id:
            provider = self.env['ftp.provider'].browse(provider_id)
            res['current_folder_id'] = provider.gdrive_folder_id or 'root'
        return res

    def action_refresh(self):
        """Rafraîchir le contenu du dossier courant."""
        self.ensure_one()
        self._load_folder_content()
        return self._reopen_wizard()

    def action_go_up(self):
        """Remonter au dossier parent."""
        self.ensure_one()
        from ..models.backend import get_backend

        if not self.current_folder_id or self.current_folder_id == "root":
            return self._reopen_wizard()

        try:
            with get_backend(self.provider_id, self.env) as backend:
                path = backend.get_folder_path(self.current_folder_id)
                if len(path) > 1:
                    # Remonter d'un niveau
                    parent = path[-2]
                    self.current_folder_id = parent.get("id", "root")
                else:
                    self.current_folder_id = "root"
        except Exception as e:
            _logger.warning("Error going up: %s", e)
            self.current_folder_id = "root"

        self._load_folder_content()
        return self._reopen_wizard()

    def action_navigate_to_folder(self, folder_id):
        """Naviguer vers un dossier spécifique."""
        self.ensure_one()
        self.current_folder_id = folder_id or "root"
        self._load_folder_content()
        return self._reopen_wizard()

    def action_select_folder(self):
        """Sélectionner le dossier courant comme dossier d'import/export."""
        self.ensure_one()
        folder_id = self.current_folder_id if self.current_folder_id != "root" else ""

        if self.target_field == 'import':
            self.provider_id.write({'gdrive_folder_id': folder_id})
            message = _("Dossier d'import défini: %s") % (self.current_folder_name or "Racine")
        else:
            self.provider_id.write({'gdrive_export_folder_id': folder_id})
            message = _("Dossier d'export défini: %s") % (self.current_folder_name or "Racine")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Dossier sélectionné'),
                'message': message,
                'sticky': False,
                'type': 'success',
            },
        }

    def _load_folder_content(self):
        """Charger le contenu du dossier courant."""
        self.ensure_one()
        from ..models.backend import get_backend

        # Supprimer les items existants
        self.item_ids.unlink()

        try:
            with get_backend(self.provider_id, self.env) as backend:
                # Lister avec dossiers inclus
                items = backend.list_files(
                    self.current_folder_id or "root",
                    include_folders=True,
                    limit=200,
                )

                # Créer les items
                ItemModel = self.env['ftp.gdrive.browser.wizard.item']
                for idx, item in enumerate(items):
                    ItemModel.create({
                        'wizard_id': self.id,
                        'sequence': idx,
                        'name': item.get('name', ''),
                        'is_folder': item.get('is_folder', False),
                        'folder_id': item.get('folder_id', ''),
                        'file_id': item.get('path', '').replace('gdrive://', '') if not item.get('is_folder') else '',
                        'size': item.get('size', 0),
                    })
        except Exception as e:
            raise UserError(_("Erreur lors du chargement: %s") % str(e))

    def _reopen_wizard(self):
        """Réouvrir le wizard avec les nouvelles données."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }


class GDriveBrowserWizardItem(models.TransientModel):
    """Item dans le navigateur Google Drive (dossier ou fichier)."""
    _name = "ftp.gdrive.browser.wizard.item"
    _description = "Item Google Drive"
    _order = "is_folder desc, name"

    wizard_id = fields.Many2one(
        "ftp.gdrive.browser.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Nom")
    is_folder = fields.Boolean(string="Dossier", default=False)
    folder_id = fields.Char(string="ID Dossier")
    file_id = fields.Char(string="ID Fichier")
    size = fields.Integer(string="Taille")
    size_display = fields.Char(string="Taille affiché",
                               compute="_compute_size_display")
    icon = fields.Char(string="Icône", compute="_compute_icon")

    @api.depends('size', 'is_folder')
    def _compute_size_display(self):
        for item in self:
            if item.is_folder:
                item.size_display = "-"
            elif item.size == 0:
                item.size_display = "-"
            elif item.size < 1024:
                item.size_display = f"{item.size} B"
            elif item.size < 1024 * 1024:
                item.size_display = f"{item.size / 1024:.1f} KB"
            else:
                item.size_display = f"{item.size / (1024 * 1024):.1f} MB"

    @api.depends('is_folder', 'name')
    def _compute_icon(self):
        for item in self:
            if item.is_folder:
                item.icon = "📁"
            elif item.name.lower().endswith(('.csv', '.txt')):
                item.icon = "📄"
            elif item.name.lower().endswith(('.xlsx', '.xls')):
                item.icon = "📊"
            elif item.name.lower().endswith(('.jpg', '.png', '.gif')):
                item.icon = "🖼️"
            elif item.name.lower().endswith('.pdf'):
                item.icon = "📕"
            else:
                item.icon = "📄"

    def action_open_folder(self):
        """Ouvrir ce dossier."""
        self.ensure_one()
        if not self.is_folder:
            return
        return self.wizard_id.action_navigate_to_folder(self.folder_id)

    def action_select_as_import_folder(self):
        """Sélectionner ce dossier comme dossier d'import."""
        self.ensure_one()
        if not self.is_folder:
            raise UserError(_("Seuls les dossiers peuvent être sélectionnés."))
        self.wizard_id.provider_id.write({'gdrive_folder_id': self.folder_id})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Dossier d\'import défini'),
                'message': _("Dossier d'import: %s") % self.name,
                'sticky': False,
                'type': 'success',
            },
        }
