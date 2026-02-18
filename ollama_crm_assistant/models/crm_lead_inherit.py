# -*- coding: utf-8 -*-
import logging

from datetime import timedelta

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class CrmLeadAI(models.Model):
    _inherit = ['crm.lead', 'ollama.mixin']

    # ------------------------------------------------------------------
    # AI Scoring Fields
    # ------------------------------------------------------------------
    ai_score = fields.Integer(
        string='AI Score',
        default=0,
        help='AI-generated lead quality score from 0 (cold) to 100 (hot).',
    )
    ai_score_reason = fields.Text(
        string='AI Score Reason',
        help='Explanation of why the AI assigned this score.',
    )
    ai_suggested_action = fields.Text(
        string='AI Suggested Action',
        help='AI-recommended next action for this lead.',
    )
    ai_follow_up_date = fields.Date(
        string='AI Follow-up Date',
        help='AI-suggested date for the next follow-up.',
    )
    ai_email_analysis = fields.Text(
        string='AI Email Analysis',
        help='AI-generated summary of the email conversation thread.',
    )
    ai_score_date = fields.Datetime(
        string='AI Score Date',
        help='Date and time when the AI last scored this lead.',
    )

    # ------------------------------------------------------------------
    # Score Lead with AI
    # ------------------------------------------------------------------
    def action_score_lead(self):
        """Score the lead using AI analysis.

        Builds a prompt from lead data, asks the AI for a JSON response
        containing score, reason, suggested_action and follow_up_days,
        then writes the parsed values to the lead.
        """
        self.ensure_one()

        # Build context from lead data
        stage_name = self.stage_id.name if self.stage_id else 'Unknown'
        tags = ', '.join(self.tag_ids.mapped('name')) if self.tag_ids else 'None'

        prompt = (
            "You are a CRM lead scoring expert. Analyze the following lead "
            "and return a JSON object with exactly these keys:\n"
            "- \"score\": integer 0-100 (0 = very cold, 100 = extremely hot)\n"
            "- \"reason\": string explaining the score\n"
            "- \"suggested_action\": string with the recommended next step\n"
            "- \"follow_up_days\": integer (number of days from today for next follow-up)\n\n"
            "Lead Information:\n"
            f"- Name: {self.name or 'N/A'}\n"
            f"- Contact: {self.partner_name or 'N/A'}\n"
            f"- Email: {self.email_from or 'N/A'}\n"
            f"- Phone: {self.phone or 'N/A'}\n"
            f"- Expected Revenue: {self.expected_revenue or 0}\n"
            f"- Probability: {self.probability or 0}%\n"
            f"- Stage: {stage_name}\n"
            f"- Description: {(self.description or 'No description')[:500]}\n"
            f"- Tags: {tags}\n\n"
            "Return ONLY valid JSON, nothing else."
        )

        system_prompt = (
            "You are an expert CRM analyst. You evaluate sales leads and "
            "provide structured scoring in JSON format. Be precise and concise."
        )

        response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            log_model='crm.lead',
            log_res_id=self.id,
        )

        if not response:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('AI Scoring Failed'),
                    'message': _('No response from AI. Check your AI configuration.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # Parse JSON response
        data = self._parse_json_response(response)
        if not data or not isinstance(data, dict):
            _logger.warning(
                "Could not parse AI score response for lead %s: %s",
                self.id, response[:200],
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('AI Scoring'),
                    'message': _('Could not parse AI response. Please try again.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # Extract and validate values
        score = data.get('score', 0)
        if isinstance(score, (int, float)):
            score = max(0, min(100, int(score)))
        else:
            score = 0

        follow_up_days = data.get('follow_up_days', 7)
        if isinstance(follow_up_days, (int, float)):
            follow_up_days = max(1, min(365, int(follow_up_days)))
        else:
            follow_up_days = 7

        self.write({
            'ai_score': score,
            'ai_score_reason': data.get('reason', ''),
            'ai_suggested_action': data.get('suggested_action', ''),
            'ai_follow_up_date': fields.Date.today() + timedelta(days=follow_up_days),
            'ai_score_date': fields.Datetime.now(),
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('AI Score: %s/100') % score,
                'message': data.get('reason', _('Lead scored successfully.')),
                'type': 'success',
                'sticky': False,
            },
        }

    # ------------------------------------------------------------------
    # Analyze Emails with AI
    # ------------------------------------------------------------------
    def action_analyze_emails(self):
        """Analyze the email thread of this lead using AI.

        Fetches the last 10 messages, builds a conversation prompt,
        and asks the AI for a summary with key points, sentiment,
        urgency level, and recommended next steps.
        """
        self.ensure_one()

        # Fetch last 10 messages for this lead
        messages = self.env['mail.message'].search([
            ('model', '=', 'crm.lead'),
            ('res_id', '=', self.id),
            ('message_type', 'in', ['email', 'comment']),
            ('body', '!=', False),
            ('body', '!=', ''),
        ], order='date desc', limit=10)

        if not messages:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Emails Found'),
                    'message': _('No email messages found for this lead.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # Build conversation history
        conversation_parts = []
        for msg in reversed(messages):
            author = msg.author_id.name if msg.author_id else msg.email_from or 'Unknown'
            date_str = fields.Datetime.to_string(msg.date) if msg.date else 'N/A'
            # Strip HTML from body for cleaner prompt
            body = msg.body or ''
            # Simple HTML stripping
            import re
            body_text = re.sub(r'<[^>]+>', '', body).strip()
            if body_text:
                conversation_parts.append(
                    f"[{date_str}] {author}:\n{body_text[:300]}"
                )

        if not conversation_parts:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Content'),
                    'message': _('No message content found to analyze.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        conversation = '\n\n'.join(conversation_parts)

        prompt = (
            "You are analyzing an email thread for a CRM lead. "
            "Provide a comprehensive analysis including:\n"
            "1. Key Points: Main topics discussed\n"
            "2. Sentiment: Overall tone (positive/neutral/negative)\n"
            "3. Urgency Level: Low / Medium / High / Critical\n"
            "4. Recommended Next Step: What action should be taken\n\n"
            f"Lead: {self.name or 'N/A'}\n"
            f"Contact: {self.partner_name or self.email_from or 'N/A'}\n\n"
            f"Email Thread ({len(conversation_parts)} messages):\n"
            f"---\n{conversation}\n---\n\n"
            "Provide a clear, structured analysis."
        )

        system_prompt = (
            "You are an expert sales communication analyst. "
            "Analyze email threads and provide actionable insights "
            "for sales teams. Be structured and concise."
        )

        response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            log_model='crm.lead',
            log_res_id=self.id,
        )

        if not response:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Email Analysis Failed'),
                    'message': _('No response from AI. Check your AI configuration.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        self.write({
            'ai_email_analysis': response,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Email Analysis Complete'),
                'message': _('Analyzed %d messages. See the AI Analysis tab.') % len(conversation_parts),
                'type': 'success',
                'sticky': False,
            },
        }
