# -*- coding: utf-8 -*-
from odoo import models, fields


class OllamaEmailTemplate(models.Model):
    _name = 'ollama.email.template'
    _description = 'AI Email Template'
    _order = 'sequence, id'

    name = fields.Char(string='Template Name', required=True)
    tone = fields.Selection([
        ('professional', 'Professional'),
        ('friendly', 'Friendly'),
        ('formal', 'Formal'),
        ('persuasive', 'Persuasive'),
        ('apology', 'Apology'),
        ('custom', 'Custom'),
    ], string='Tone', required=True, default='professional')
    system_prompt = fields.Text(
        string='System Prompt',
        required=True,
        help="Instructions for the AI when generating emails with this tone.",
    )
    example = fields.Text(
        string='Example Output',
        help="An example email to guide the AI style.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
