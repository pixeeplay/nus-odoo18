# -*- coding: utf-8 -*-
from odoo import models, fields


class HelpdeskStage(models.Model):
    _name = 'ollama.helpdesk.stage'
    _description = 'Helpdesk Ticket Stage'
    _order = 'sequence, id'

    name = fields.Char(string='Stage Name', required=True, translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    fold = fields.Boolean(
        string='Folded in Kanban',
        help="If checked, this stage will be folded in the kanban view.",
    )
    is_closing = fields.Boolean(
        string='Closing Stage',
        help="Tickets in this stage are considered resolved/closed.",
    )
