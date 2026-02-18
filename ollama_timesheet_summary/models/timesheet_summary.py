# -*- coding: utf-8 -*-
import json
import logging
from collections import defaultdict
from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TimesheetAiSummary(models.Model):
    _name = 'timesheet.ai.summary'
    _description = 'AI Timesheet Weekly Summary'
    _inherit = ['ollama.mixin']
    _order = 'week_start desc, employee_id'

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
        ondelete='cascade', index=True,
    )
    week_start = fields.Date(string='Week Start', required=True, index=True)
    week_end = fields.Date(string='Week End', required=True)
    total_hours = fields.Float(string='Total Hours', digits=(10, 2))
    project_breakdown = fields.Text(
        string='Project Breakdown',
        help='JSON or formatted text showing hours per project.',
    )
    ai_summary = fields.Html(string='AI Summary')
    ai_highlights = fields.Text(
        string='Key Highlights',
        help='Key accomplishments identified by AI.',
    )
    ai_concerns = fields.Text(
        string='Concerns / Alerts',
        help='Overtime, underlogging, anomalies detected by AI.',
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('sent', 'Sent'),
    ], string='Status', default='draft', required=True, tracking=True)

    _sql_constraints = [
        ('employee_week_unique',
         'unique(employee_id, week_start)',
         'A summary already exists for this employee and week.'),
    ]

    # ------------------------------------------------------------------
    # Compute display name
    # ------------------------------------------------------------------
    @api.depends('employee_id', 'week_start')
    def _compute_display_name(self):
        for rec in self:
            emp = rec.employee_id.name or _('Unknown')
            week = rec.week_start or ''
            rec.display_name = f"{emp} - {week}"

    # ------------------------------------------------------------------
    # Generate AI Summary
    # ------------------------------------------------------------------
    def action_generate_summary(self):
        """Fetch timesheets for the employee/week and generate AI summary."""
        for rec in self:
            # Fetch analytic lines (timesheets) for this employee and week
            lines = self.env['account.analytic.line'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('date', '>=', rec.week_start),
                ('date', '<=', rec.week_end),
                ('project_id', '!=', False),
            ])

            if not lines:
                rec.write({
                    'total_hours': 0.0,
                    'project_breakdown': _('No timesheet entries found.'),
                    'ai_summary': '<p>%s</p>' % _('No timesheet entries for this week.'),
                    'ai_highlights': '',
                    'ai_concerns': _('No timesheets logged this week.'),
                    'state': 'generated',
                })
                continue

            # Build project breakdown
            breakdown = defaultdict(lambda: {'total_hours': 0.0, 'tasks': []})
            total = 0.0

            for line in lines:
                project_name = line.project_id.name or _('Unknown Project')
                hours = line.unit_amount or 0.0
                task_name = line.task_id.name if line.task_id else _('No Task')
                description = line.name or ''

                breakdown[project_name]['total_hours'] += hours
                breakdown[project_name]['tasks'].append({
                    'name': task_name,
                    'hours': round(hours, 2),
                    'description': description,
                })
                total += hours

            # Convert to serializable dict
            breakdown_dict = {}
            for proj, data in breakdown.items():
                breakdown_dict[proj] = {
                    'total_hours': round(data['total_hours'], 2),
                    'tasks': data['tasks'],
                }

            breakdown_json = json.dumps(breakdown_dict, indent=2, ensure_ascii=False)

            # Build the AI prompt
            employee_name = rec.employee_id.name
            week_start_str = fields.Date.to_string(rec.week_start)
            week_end_str = fields.Date.to_string(rec.week_end)

            system_prompt = (
                "You are an expert HR assistant that analyzes employee timesheets. "
                "You produce clear, concise weekly summaries in JSON format. "
                "Be professional and constructive. Identify achievements and "
                "potential issues (overtime, underlogging, scattered focus)."
            )

            prompt = f"""Analyze the following timesheet data and produce a JSON response.

Employee: {employee_name}
Week: {week_start_str} to {week_end_str}
Total Hours Logged: {round(total, 2)}

Project Breakdown:
{breakdown_json}

Please respond with a valid JSON object containing exactly these keys:
{{
    "summary": "A concise paragraph (3-5 sentences) summarizing what the employee worked on this week, their focus areas, and overall productivity.",
    "highlights": ["List of 2-5 key accomplishments or notable work items"],
    "concerns": ["List of 0-3 potential concerns such as overtime (>40h), underlogging (<30h for full-time), scattered across too many projects, or any anomalies"]
}}

If total hours are below 30, flag underlogging.
If total hours exceed 45, flag overtime.
If the employee worked on more than 5 projects, note scattered focus.
Respond ONLY with the JSON object, no additional text."""

            # Call AI via the mixin
            response = rec._call_ollama_safe(
                prompt,
                system_prompt=system_prompt,
                max_tokens=1500,
                temperature=0.3,
                log_model=self._name,
                log_res_id=rec.id,
            )

            # Parse the AI response
            parsed = self._parse_json_response(response)

            if parsed and isinstance(parsed, dict):
                summary_text = parsed.get('summary', '')
                highlights_list = parsed.get('highlights', [])
                concerns_list = parsed.get('concerns', [])

                # Format summary as HTML
                ai_summary_html = '<p>%s</p>' % summary_text if summary_text else ''

                # Format highlights and concerns as text
                highlights_text = '\n'.join(
                    f"- {h}" for h in highlights_list
                ) if highlights_list else ''
                concerns_text = '\n'.join(
                    f"- {c}" for c in concerns_list
                ) if concerns_list else ''
            else:
                # Fallback: use raw response
                ai_summary_html = '<p>%s</p>' % (response or _('AI response could not be parsed.'))
                highlights_text = ''
                concerns_text = ''

            rec.write({
                'total_hours': round(total, 2),
                'project_breakdown': breakdown_json,
                'ai_summary': ai_summary_html,
                'ai_highlights': highlights_text,
                'ai_concerns': concerns_text,
                'state': 'generated',
            })

        return True

    # ------------------------------------------------------------------
    # Send Summary
    # ------------------------------------------------------------------
    def action_send_summary(self):
        """Mark the summary as sent. Optionally post a message."""
        for rec in self:
            if rec.state != 'generated':
                raise UserError(_(
                    'Summary must be generated before it can be sent.'
                ))
            # Post an internal note if mail.thread is available
            if hasattr(rec.employee_id, 'message_post'):
                try:
                    body = _(
                        '<strong>AI Timesheet Summary</strong> for week '
                        '%(start)s to %(end)s:<br/>%(summary)s',
                        start=rec.week_start,
                        end=rec.week_end,
                        summary=rec.ai_summary or '',
                    )
                    rec.employee_id.message_post(
                        body=body,
                        subject=_('AI Timesheet Summary - %s') % rec.week_start,
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
                except Exception as e:
                    _logger.warning("Could not post summary message: %s", e)

            rec.state = 'sent'

        return True

    # ------------------------------------------------------------------
    # Reset to Draft
    # ------------------------------------------------------------------
    def action_reset_draft(self):
        """Reset summary back to draft state."""
        for rec in self:
            rec.write({
                'state': 'draft',
                'ai_summary': False,
                'ai_highlights': False,
                'ai_concerns': False,
            })
        return True

    # ------------------------------------------------------------------
    # Cron: Weekly Generation
    # ------------------------------------------------------------------
    @api.model
    def generate_weekly_summaries(self):
        """Generate weekly summaries for all active employees with timesheets.

        Called by the weekly cron job. Creates summary records for the
        previous week (Monday to Sunday) for each employee that logged time.
        """
        today = fields.Date.context_today(self)
        # Calculate previous week: Monday to Sunday
        # today.weekday(): Monday=0, Sunday=6
        days_since_monday = today.weekday()
        last_monday = today - timedelta(days=days_since_monday + 7)
        last_sunday = last_monday + timedelta(days=6)

        _logger.info(
            "Generating weekly timesheet summaries for %s to %s",
            last_monday, last_sunday,
        )

        # Find all employees who logged time last week
        timesheet_lines = self.env['account.analytic.line'].search([
            ('date', '>=', last_monday),
            ('date', '<=', last_sunday),
            ('project_id', '!=', False),
            ('employee_id', '!=', False),
        ])

        employee_ids = timesheet_lines.mapped('employee_id').ids
        if not employee_ids:
            _logger.info("No timesheet entries found for the previous week.")
            return True

        employees = self.env['hr.employee'].browse(employee_ids)
        created = 0
        errors = 0

        for employee in employees:
            # Check if summary already exists
            existing = self.search([
                ('employee_id', '=', employee.id),
                ('week_start', '=', last_monday),
            ], limit=1)

            if existing:
                _logger.info(
                    "Summary already exists for %s week %s, skipping.",
                    employee.name, last_monday,
                )
                continue

            try:
                summary = self.create({
                    'employee_id': employee.id,
                    'week_start': last_monday,
                    'week_end': last_sunday,
                    'state': 'draft',
                })
                summary.action_generate_summary()
                created += 1
            except Exception as e:
                _logger.error(
                    "Failed to generate summary for %s: %s",
                    employee.name, e,
                )
                errors += 1

        _logger.info(
            "Weekly summary generation complete. Created: %d, Errors: %d",
            created, errors,
        )
        return True
