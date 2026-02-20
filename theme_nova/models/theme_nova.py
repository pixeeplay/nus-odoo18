from odoo import models


class ThemeNova(models.AbstractModel):
    _inherit = 'theme.utils'

    def _theme_nova_post_copy(self, mod):
        # Enable first header and footer by default
        self.enable_view('theme_nova.nova_header_topbar')
        self.enable_view('theme_nova.nova_footer_columns')
