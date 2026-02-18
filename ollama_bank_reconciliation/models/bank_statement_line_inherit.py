# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class BankStatementLineAI(models.Model):
    _inherit = ['account.bank.statement.line', 'ollama.mixin']

    # ------------------------------------------------------------------
    # AI Analysis Fields
    # ------------------------------------------------------------------
    ai_match_suggestion = fields.Char(
        string='AI Match Suggestion',
        help='AI-suggested matching invoice or bill reference.',
    )
    ai_match_confidence = fields.Float(
        string='AI Confidence',
        digits=(5, 1),
        help='AI confidence score for the suggested match (0-100).',
    )
    ai_label_analysis = fields.Text(
        string='AI Label Analysis',
        help='AI interpretation of what this payment is for.',
    )
    ai_partner_suggestion = fields.Char(
        string='AI Partner Suggestion',
        help='AI-suggested partner name based on the payment label.',
    )
    ai_analysis_date = fields.Datetime(
        string='AI Analysis Date',
        help='Date and time when the AI last analyzed this statement line.',
    )

    # ------------------------------------------------------------------
    # Analyze a single statement line with AI
    # ------------------------------------------------------------------
    def action_analyze_line(self):
        """Analyze this bank statement line using AI.

        Builds a prompt with the statement line details, open invoices,
        and known partners, then asks the AI for a JSON response with
        match_suggestion, confidence, label_analysis, and partner_suggestion.
        """
        self.ensure_one()

        # -- Build context from statement line --
        line_label = self.payment_ref or ''
        line_amount = self.amount or 0.0
        line_date = fields.Date.to_string(self.date) if self.date else 'N/A'
        line_partner = self.partner_id.name if self.partner_id else 'Unknown'

        # -- Gather open invoices for matching context --
        open_invoices = self.env['account.move'].search([
            ('state', '=', 'posted'),
            ('payment_state', '!=', 'paid'),
            ('move_type', 'in', ['out_invoice', 'in_invoice']),
        ], limit=50, order='invoice_date desc')

        invoice_lines = []
        for inv in open_invoices:
            inv_ref = inv.name or inv.ref or 'N/A'
            inv_partner = inv.partner_id.name if inv.partner_id else 'Unknown'
            inv_amount = inv.amount_residual or inv.amount_total or 0.0
            inv_date = fields.Date.to_string(inv.invoice_date) if inv.invoice_date else 'N/A'
            inv_type = 'Invoice' if inv.move_type == 'out_invoice' else 'Bill'
            invoice_lines.append(
                f"  - {inv_type} {inv_ref}: partner={inv_partner}, "
                f"amount={inv_amount:.2f}, date={inv_date}"
            )

        invoices_text = '\n'.join(invoice_lines) if invoice_lines else '  (none)'

        # -- Gather known partners --
        partners = self.env['res.partner'].search([
            ('is_company', '=', True),
            ('active', '=', True),
        ], limit=100, order='name')
        partner_names = ', '.join(partners.mapped('name')) if partners else '(none)'

        # -- Build the prompt --
        prompt = (
            "You are a bank reconciliation expert. Analyze the following bank "
            "statement line and suggest the best matching invoice or bill.\n\n"
            "Bank Statement Line:\n"
            f"- Label: {line_label}\n"
            f"- Amount: {line_amount:.2f}\n"
            f"- Date: {line_date}\n"
            f"- Current Partner: {line_partner}\n\n"
            "Open Invoices/Bills:\n"
            f"{invoices_text}\n\n"
            "Known Partners:\n"
            f"{partner_names}\n\n"
            "Return ONLY a valid JSON object with these keys:\n"
            "- \"match_suggestion\": string — reference of the best matching "
            "invoice/bill (e.g. 'INV/2024/0042'), or 'No match found'\n"
            "- \"confidence\": float 0-100 — your confidence in this match\n"
            "- \"label_analysis\": string — what you think this payment is for "
            "(1-2 sentences)\n"
            "- \"partner_suggestion\": string — the most likely partner name "
            "from the known partners list, or 'Unknown'\n\n"
            "Return ONLY valid JSON, nothing else."
        )

        system_prompt = (
            "You are an expert accountant specializing in bank reconciliation. "
            "You analyze bank statement lines and match them to open invoices "
            "and bills. You respond only in valid JSON format."
        )

        response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            temperature=0.2,
            log_model='account.bank.statement.line',
            log_res_id=self.id,
        )

        if not response:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('AI Analysis Failed'),
                    'message': _('No response from AI. Check your AI configuration.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # -- Parse JSON response --
        data = self._parse_json_response(response)
        if not data or not isinstance(data, dict):
            _logger.warning(
                "Could not parse AI response for statement line %s: %s",
                self.id, response[:200],
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('AI Analysis'),
                    'message': _('Could not parse AI response. Please try again.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # -- Extract and validate values --
        confidence = data.get('confidence', 0)
        if isinstance(confidence, (int, float)):
            confidence = max(0.0, min(100.0, float(confidence)))
        else:
            confidence = 0.0

        match_suggestion = data.get('match_suggestion', '') or ''
        label_analysis = data.get('label_analysis', '') or ''
        partner_suggestion = data.get('partner_suggestion', '') or ''

        self.write({
            'ai_match_suggestion': match_suggestion[:255],
            'ai_match_confidence': confidence,
            'ai_label_analysis': label_analysis,
            'ai_partner_suggestion': partner_suggestion[:255],
            'ai_analysis_date': fields.Datetime.now(),
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('AI Analysis Complete'),
                'message': _(
                    'Match: %s (%.0f%% confidence)\n%s'
                ) % (match_suggestion or _('No match'), confidence, label_analysis or ''),
                'type': 'success',
                'sticky': True,
            },
        }
