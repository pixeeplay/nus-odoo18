# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EmailComposeAIWizard(models.TransientModel):
    _name = 'email.compose.ai.wizard'
    _description = 'AI Email Compose Wizard'
    _inherit = ['ollama.mixin']

    tone = fields.Selection([
        ('professional', 'Professional'),
        ('friendly', 'Friendly'),
        ('formal', 'Formal'),
        ('persuasive', 'Persuasive'),
        ('apology', 'Apology'),
    ], string='Tone', default='professional', required=True)
    template_id = fields.Many2one(
        'ollama.email.template', string='Template',
        domain="[('active', '=', True)]",
    )
    instruction = fields.Text(
        string='Instructions',
        required=True,
        help="Describe what you want the email to say. E.g.: 'Follow up on the last meeting, "
             "propose a new date next week, mention the budget review.'",
    )
    recipient_name = fields.Char(string='Recipient Name')
    context_info = fields.Text(
        string='Additional Context',
        help="Paste previous emails or conversation context to help the AI.",
    )
    generated_subject = fields.Char(string='Generated Subject', readonly=True)
    generated_body = fields.Html(string='Generated Email', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Generated'),
    ], default='draft')

    @api.onchange('tone')
    def _onchange_tone(self):
        if self.tone:
            template = self.env['ollama.email.template'].search([
                ('tone', '=', self.tone), ('active', '=', True),
            ], limit=1)
            if template:
                self.template_id = template.id

    def action_generate(self):
        """Generate email using AI."""
        self.ensure_one()
        if not self.instruction:
            raise UserError(_("Please provide instructions for the email."))

        config = self._get_ollama_config()

        # Build system prompt from template or default
        if self.template_id and self.template_id.system_prompt:
            system_prompt = self.template_id.system_prompt
        else:
            system_prompt = self._get_default_system_prompt()

        # Build user prompt
        parts = [f"Tone: {self.tone}"]
        if self.recipient_name:
            parts.append(f"Recipient: {self.recipient_name}")
        parts.append(f"Instructions: {self.instruction}")
        if self.context_info:
            parts.append(f"Context:\n{self.context_info}")

        prompt = '\n'.join(parts)

        result = self._call_ollama_safe(
            prompt=prompt,
            system_prompt=system_prompt,
            log_model='email.compose.ai.wizard',
            log_res_id=self.id,
            config=config,
        )

        if not result:
            raise UserError(_("AI did not return a response. Check your AI configuration."))

        # Try to extract subject from response
        subject = ''
        body = result
        for prefix in ['Subject:', 'Objet:', 'Subject :', 'Objet :']:
            if prefix in result:
                lines = result.split('\n')
                for i, line in enumerate(lines):
                    if line.strip().startswith(prefix):
                        subject = line.strip()[len(prefix):].strip()
                        body = '\n'.join(lines[i + 1:]).strip()
                        break
                if subject:
                    break

        # Store tone in log for dashboard stats
        self.env['ollama.log'].sudo().search([
            ('res_model', '=', 'email.compose.ai.wizard'),
            ('res_id', '=', self.id),
        ], limit=1, order='id desc').write({
            'prompt_preview': f'[{self.tone}] {(self.instruction or "")[:400]}',
        })

        # Convert newlines to HTML
        body_html = body.replace('\n', '<br/>')

        self.write({
            'generated_subject': subject or _('(AI-generated email)'),
            'generated_body': body_html,
            'state': 'done',
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Email Result'),
            'res_model': 'email.compose.ai.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_copy_to_clipboard(self):
        """Return the generated content for the user to copy."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Email Generated'),
                'message': _('Copy the email above and paste it into your composer.'),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_regenerate(self):
        """Reset and let user regenerate."""
        self.write({'state': 'draft', 'generated_body': False, 'generated_subject': False})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Compose with AI'),
            'res_model': 'email.compose.ai.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @staticmethod
    def _get_default_system_prompt():
        return (
            "You are a professional email writer. "
            "Write a clear, well-structured email based on the user's instructions. "
            "Always start with 'Subject: <subject line>' on the first line, "
            "followed by the email body. "
            "Adapt your tone to the requested style. "
            "Keep emails concise and actionable. "
            "Write in the same language as the user's instructions."
        )
