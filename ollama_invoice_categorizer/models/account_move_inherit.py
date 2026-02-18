# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = ['account.move', 'ollama.mixin']
    _name = 'account.move'

    # ------------------------------------------------------------------
    # AI Analysis Fields
    # ------------------------------------------------------------------
    ai_account_suggestion = fields.Char(
        string='AI Account Suggestion',
        help='Account name or code suggested by the AI engine.',
        tracking=True,
    )
    ai_account_confidence = fields.Float(
        string='AI Confidence (%)',
        digits=(5, 1),
        help='Confidence score from 0 to 100 percent.',
    )
    ai_anomaly_detected = fields.Boolean(
        string='Anomaly Detected',
        help='Whether the AI flagged an anomaly on this invoice.',
    )
    ai_anomaly_description = fields.Text(
        string='Anomaly Description',
        help='Details about the anomaly detected by the AI.',
    )
    ai_expense_category = fields.Char(
        string='Expense Category',
        help='AI-assigned expense category (e.g. Office Supplies, Travel, IT Services).',
        tracking=True,
    )
    ai_analysis_date = fields.Datetime(
        string='Last AI Analysis',
        help='Date and time of the last AI analysis.',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # AI Analysis Action
    # ------------------------------------------------------------------
    def action_analyze_invoice(self):
        """Analyze vendor invoice with AI: suggest account, detect anomalies,
        categorize expense."""
        self.ensure_one()

        if self.move_type != 'in_invoice':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Not a Vendor Bill'),
                    'message': _('AI analysis is only available for vendor bills.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        config = self._get_ollama_config()

        # ----- Build context -----
        partner_name = self.partner_id.name or 'Unknown Vendor'

        # Invoice lines context
        lines_info = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            lines_info.append({
                'product': line.product_id.name or line.name or 'N/A',
                'quantity': line.quantity,
                'price_unit': line.price_unit,
                'subtotal': line.price_subtotal,
                'account': line.account_id.display_name or 'N/A',
                'account_code': line.account_id.code or 'N/A',
            })

        total_amount = self.amount_total

        # Previous invoices from same vendor (last 5 account codes used)
        history_accounts = []
        if self.partner_id:
            previous_moves = self.env['account.move'].search([
                ('partner_id', '=', self.partner_id.id),
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
                ('id', '!=', self.id),
            ], order='invoice_date desc', limit=10)
            for move in previous_moves:
                for line in move.invoice_line_ids.filtered(
                    lambda l: l.display_type == 'product' and l.account_id
                ):
                    code = line.account_id.code
                    if code and code not in history_accounts:
                        history_accounts.append(code)
                    if len(history_accounts) >= 5:
                        break
                if len(history_accounts) >= 5:
                    break

        # ----- Build prompt -----
        system_prompt = (
            "You are an expert accounting assistant specialized in vendor invoice "
            "analysis. You analyze invoices to suggest the correct accounting account, "
            "detect anomalies, and categorize expenses. "
            "Always respond with valid JSON only, no extra text."
        )

        lines_text = ""
        for idx, li in enumerate(lines_info, 1):
            lines_text += (
                f"  Line {idx}: Product={li['product']}, Qty={li['quantity']}, "
                f"Unit Price={li['price_unit']}, Subtotal={li['subtotal']}, "
                f"Current Account={li['account']} ({li['account_code']})\n"
            )

        if not lines_text:
            lines_text = "  (no product lines)\n"

        history_text = ', '.join(history_accounts) if history_accounts else 'No history'

        prompt = (
            f"Analyze the following vendor invoice and provide:\n"
            f"1. The most appropriate accounting account (code and name)\n"
            f"2. Whether any anomalies are detected\n"
            f"3. An expense category\n\n"
            f"Vendor: {partner_name}\n"
            f"Invoice Total: {total_amount}\n"
            f"Invoice Lines:\n{lines_text}\n"
            f"Previous account codes used for this vendor: {history_text}\n\n"
            f"Respond ONLY with a JSON object in this exact format:\n"
            '{{\n'
            '  "account_suggestion": "6XXXXX - Account Name",\n'
            '  "confidence": 85,\n'
            '  "anomaly_detected": false,\n'
            '  "anomaly_description": "Description if anomaly found, empty string otherwise",\n'
            '  "expense_category": "Office Supplies"\n'
            '}}\n\n'
            "Rules:\n"
            "- account_suggestion should be the account code followed by the account name\n"
            "- confidence is an integer from 0 to 100\n"
            "- anomaly_detected is a boolean\n"
            "- anomaly_description should explain the anomaly if detected, or be empty\n"
            "- expense_category should be a concise category like: Office Supplies, "
            "Travel, IT Services, Professional Services, Utilities, Rent, Marketing, "
            "Insurance, Maintenance, Raw Materials, Shipping, Subscriptions, etc.\n"
            "- Compare with the vendor's historical accounts to detect unusual changes"
        )

        # ----- Call AI -----
        response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            config=config,
            log_model=self._name,
            log_res_id=self.id,
        )

        if not response:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Analysis Failed'),
                    'message': _('The AI did not return a response. Check your AI configuration.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # ----- Parse response -----
        data = self._parse_json_response(response)
        if not data or not isinstance(data, dict):
            _logger.warning(
                "AI invoice analysis: could not parse response for move %s: %s",
                self.id, response[:200],
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Analysis Failed'),
                    'message': _('Could not parse AI response. Try again.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # ----- Write results -----
        confidence = data.get('confidence', 0)
        confidence = max(0.0, min(100.0, float(confidence)))

        self.write({
            'ai_account_suggestion': data.get('account_suggestion', ''),
            'ai_account_confidence': confidence,
            'ai_anomaly_detected': bool(data.get('anomaly_detected', False)),
            'ai_anomaly_description': data.get('anomaly_description', ''),
            'ai_expense_category': data.get('expense_category', ''),
            'ai_analysis_date': fields.Datetime.now(),
        })

        # Build notification message
        msg_parts = [
            _('Account: %s (%.0f%% confidence)') % (
                data.get('account_suggestion', 'N/A'), confidence),
            _('Category: %s') % data.get('expense_category', 'N/A'),
        ]
        if data.get('anomaly_detected'):
            msg_parts.append(
                _('ANOMALY: %s') % data.get('anomaly_description', 'Anomaly detected'))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Invoice Analyzed'),
                'message': '\n'.join(msg_parts),
                'type': 'warning' if data.get('anomaly_detected') else 'success',
                'sticky': True,
            },
        }

    # ------------------------------------------------------------------
    # Apply AI Suggestion
    # ------------------------------------------------------------------
    def action_apply_suggestion(self):
        """Find the account.account matching ai_account_suggestion and apply
        it to the first invoice line."""
        self.ensure_one()

        if not self.ai_account_suggestion:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No AI Suggestion'),
                    'message': _('Run AI analysis first.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        suggestion = self.ai_account_suggestion.strip()

        # Try to extract account code from the suggestion (e.g. "601100 - Purchases")
        account_code = suggestion.split(' ')[0].split('-')[0].strip()

        Account = self.env['account.account']
        account = False

        # Try matching by code first
        if account_code:
            account = Account.search([
                ('code', '=', account_code),
                ('company_id', '=', self.company_id.id),
            ], limit=1)

        # Try matching by code prefix
        if not account and account_code:
            account = Account.search([
                ('code', '=like', account_code + '%'),
                ('company_id', '=', self.company_id.id),
            ], limit=1)

        # Try matching by name (from the suggestion after the dash)
        if not account and ' - ' in suggestion:
            account_name = suggestion.split(' - ', 1)[1].strip()
            account = Account.search([
                ('name', 'ilike', account_name),
                ('company_id', '=', self.company_id.id),
            ], limit=1)

        if not account:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Account Not Found'),
                    'message': _('No matching account found for "%s". '
                                 'Create the account or adjust the suggestion.') % suggestion,
                    'type': 'warning',
                    'sticky': True,
                },
            }

        # Apply to the first product invoice line
        product_lines = self.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product'
        )
        if not product_lines:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Invoice Lines'),
                    'message': _('No product lines found to apply the account to.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        product_lines[0].account_id = account.id

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Account Applied'),
                'message': _('Account "%s" applied to the first invoice line.') % account.display_name,
                'type': 'success',
                'sticky': False,
            },
        }
