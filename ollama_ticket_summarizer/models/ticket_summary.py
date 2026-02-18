# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class TicketAiSummary(models.Model):
    _name = 'ticket.ai.summary'
    _description = 'AI Thread Summary'
    _inherit = ['ollama.mixin']
    _order = 'create_date desc'

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    res_model = fields.Char(
        string='Source Model',
        required=True,
        help='Technical model name, e.g. crm.lead, helpdesk.ticket',
    )
    res_id = fields.Integer(
        string='Source Record ID',
        required=True,
    )
    res_name = fields.Char(
        string='Source Record',
        compute='_compute_res_name',
        store=True,
    )
    message_count = fields.Integer(
        string='Messages Summarized',
    )
    ai_summary = fields.Html(
        string='Executive Summary',
    )
    ai_key_points = fields.Text(
        string='Key Discussion Points',
    )
    ai_action_items = fields.Text(
        string='Action Items',
    )
    ai_escalation_needed = fields.Boolean(
        string='Escalation Needed',
        default=False,
        help='AI detected that this thread may require escalation.',
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='Status', default='draft', required=True)

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends('res_model', 'res_id')
    def _compute_res_name(self):
        for rec in self:
            if rec.res_model and rec.res_id:
                try:
                    source = self.env[rec.res_model].sudo().browse(rec.res_id)
                    if source.exists():
                        rec.res_name = source.display_name or _('Record #%s') % rec.res_id
                    else:
                        rec.res_name = _('Deleted Record #%s') % rec.res_id
                except Exception:
                    rec.res_name = '%s #%s' % (rec.res_model, rec.res_id)
            else:
                rec.res_name = False

    # ------------------------------------------------------------------
    # Generate Summary
    # ------------------------------------------------------------------
    def action_generate_summary(self):
        """Fetch all chatter messages and generate an AI summary."""
        self.ensure_one()

        # Fetch messages for the source record
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', self.res_model),
            ('res_id', '=', self.res_id),
            ('message_type', 'in', ['comment', 'email']),
        ], order='date asc')

        if len(messages) < 3:
            raise UserError(_(
                'At least 3 messages are needed to generate a summary. '
                'This thread only has %d message(s).'
            ) % len(messages))

        # Build conversation text
        conversation_lines = []
        for msg in messages:
            author = msg.author_id.name if msg.author_id else _('Unknown')
            date_str = fields.Datetime.to_string(msg.date) if msg.date else ''
            body_text = html2plaintext(msg.body or '').strip()
            if body_text:
                conversation_lines.append(
                    '[%s] %s:\n%s' % (date_str, author, body_text)
                )

        if not conversation_lines:
            raise UserError(_('No message content found to summarize.'))

        conversation_text = '\n\n'.join(conversation_lines)

        # Truncate if too long (keep last portion for context window)
        max_chars = 12000
        if len(conversation_text) > max_chars:
            conversation_text = (
                '... (earlier messages truncated) ...\n\n'
                + conversation_text[-max_chars:]
            )

        # Build AI prompt
        system_prompt = (
            "You are an expert business analyst. You analyze conversation threads "
            "and produce structured summaries. Always respond in valid JSON format."
        )
        prompt = (
            "Summarize this conversation thread. Provide your response as a JSON object "
            "with these keys:\n"
            "- \"executive_summary\": a concise HTML paragraph summarizing the thread\n"
            "- \"key_points\": a list of key discussion points (plain text strings)\n"
            "- \"action_items\": a list of action items extracted (plain text strings)\n"
            "- \"escalation_needed\": boolean, true if the thread indicates urgency, "
            "frustration, unresolved issues, or need for management attention\n\n"
            "Conversation:\n\n%s"
        ) % conversation_text

        # Call AI
        raw_response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            max_tokens=2000,
            temperature=0.3,
            log_model=self._name,
            log_res_id=self.id,
        )

        if not raw_response:
            self.write({'state': 'error'})
            raise UserError(_('AI returned an empty response. Check your AI configuration.'))

        # Parse JSON response
        parsed = self._parse_json_response(raw_response)
        if not parsed or not isinstance(parsed, dict):
            # Fallback: store raw response as summary
            self.write({
                'ai_summary': '<p>%s</p>' % raw_response.replace('\n', '<br/>'),
                'message_count': len(messages),
                'state': 'done',
            })
            return True

        # Write parsed results
        key_points = parsed.get('key_points', [])
        action_items = parsed.get('action_items', [])

        vals = {
            'ai_summary': parsed.get('executive_summary', raw_response),
            'ai_key_points': '\n'.join(
                '- %s' % p if isinstance(p, str) else str(p)
                for p in key_points
            ) if key_points else '',
            'ai_action_items': '\n'.join(
                '- %s' % a if isinstance(a, str) else str(a)
                for a in action_items
            ) if action_items else '',
            'ai_escalation_needed': bool(parsed.get('escalation_needed', False)),
            'message_count': len(messages),
            'state': 'done',
        }
        self.write(vals)
        return True

    # ------------------------------------------------------------------
    # Class-level helper
    # ------------------------------------------------------------------
    @api.model
    def action_summarize_record(self, res_model, res_id):
        """Create or update a summary for a specific record.

        :param res_model: Technical model name (e.g. 'crm.lead')
        :param res_id: Record ID
        :returns: The ticket.ai.summary record
        """
        existing = self.search([
            ('res_model', '=', res_model),
            ('res_id', '=', res_id),
        ], limit=1)

        if existing:
            summary = existing
        else:
            summary = self.create({
                'res_model': res_model,
                'res_id': res_id,
                'state': 'draft',
            })

        summary.action_generate_summary()
        return summary
