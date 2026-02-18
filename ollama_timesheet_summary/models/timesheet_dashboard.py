# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TimesheetSummaryDashboard(models.TransientModel):
    _name = 'timesheet.summary.dashboard'
    _description = 'Timesheet Summary Dashboard'

    # ------------------------------------------------------------------
    # Statistic fields
    # ------------------------------------------------------------------
    total_employees = fields.Integer(
        string='Employees with Timesheets', readonly=True,
    )
    summaries_generated = fields.Integer(
        string='Summaries Generated', readonly=True,
    )
    summaries_sent = fields.Integer(
        string='Summaries Sent', readonly=True,
    )
    avg_hours_week = fields.Float(
        string='Avg Hours / Week', digits=(10, 1), readonly=True,
    )
    overtime_alerts = fields.Integer(
        string='Overtime Alerts', readonly=True,
    )
    underlog_alerts = fields.Integer(
        string='Underlog Alerts', readonly=True,
    )
    ai_provider = fields.Char(string='AI Provider', readonly=True)
    ai_model = fields.Char(string='AI Model', readonly=True)

    # ------------------------------------------------------------------
    # Default values: compute stats
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        Summary = self.env['timesheet.ai.summary']

        # Current week boundaries
        today = fields.Date.context_today(self)
        days_since_monday = today.weekday()
        this_monday = today - timedelta(days=days_since_monday)
        last_monday = this_monday - timedelta(days=7)
        last_sunday = last_monday + timedelta(days=6)

        # All summaries for last week
        last_week_summaries = Summary.search([
            ('week_start', '=', last_monday),
        ])

        # All summaries overall
        all_summaries = Summary.search([])

        total_employees = len(last_week_summaries.mapped('employee_id'))
        generated = len(last_week_summaries.filtered(
            lambda s: s.state in ('generated', 'sent')
        ))
        sent = len(last_week_summaries.filtered(
            lambda s: s.state == 'sent'
        ))

        # Average hours from last week summaries
        avg_hours = 0.0
        if last_week_summaries:
            hours_list = last_week_summaries.mapped('total_hours')
            avg_hours = sum(hours_list) / len(hours_list) if hours_list else 0.0

        # Overtime (>45h) and underlog (<30h) alerts from last week
        overtime = len(last_week_summaries.filtered(
            lambda s: s.total_hours > 45.0
        ))
        underlog = len(last_week_summaries.filtered(
            lambda s: 0 < s.total_hours < 30.0
        ))

        # AI config info
        provider_name = ''
        model_name = ''
        try:
            config = self.env['ollama.config'].get_active_config()
            provider_name = config.get_provider_display()
            model_name = config._get_model_name()
        except UserError:
            provider_name = _('Not Configured')
            model_name = _('N/A')

        res.update({
            'total_employees': total_employees,
            'summaries_generated': generated,
            'summaries_sent': sent,
            'avg_hours_week': round(avg_hours, 1),
            'overtime_alerts': overtime,
            'underlog_alerts': underlog,
            'ai_provider': provider_name,
            'ai_model': model_name,
        })
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_generate_this_week(self):
        """Generate summaries for the current week (up to today)."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        days_since_monday = today.weekday()
        this_monday = today - timedelta(days=days_since_monday)

        Summary = self.env['timesheet.ai.summary']

        # Find employees with timesheets this week
        timesheet_lines = self.env['account.analytic.line'].search([
            ('date', '>=', this_monday),
            ('date', '<=', today),
            ('project_id', '!=', False),
            ('employee_id', '!=', False),
        ])

        employee_ids = timesheet_lines.mapped('employee_id').ids
        if not employee_ids:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Timesheets'),
                    'message': _('No timesheet entries found for this week.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        employees = self.env['hr.employee'].browse(employee_ids)
        created = 0
        errors = 0
        this_sunday = this_monday + timedelta(days=6)

        for employee in employees:
            existing = Summary.search([
                ('employee_id', '=', employee.id),
                ('week_start', '=', this_monday),
            ], limit=1)

            if existing:
                # Regenerate if in draft
                if existing.state == 'draft':
                    try:
                        existing.action_generate_summary()
                        created += 1
                    except Exception as e:
                        _logger.warning(
                            "Failed to regenerate for %s: %s",
                            employee.name, e,
                        )
                        errors += 1
                continue

            try:
                summary = Summary.create({
                    'employee_id': employee.id,
                    'week_start': this_monday,
                    'week_end': this_sunday,
                    'state': 'draft',
                })
                summary.action_generate_summary()
                created += 1
            except Exception as e:
                _logger.warning(
                    "Failed to generate for %s: %s", employee.name, e,
                )
                errors += 1

        msg_type = 'success' if errors == 0 else 'warning'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Generation Complete'),
                'message': _('Generated %d summaries. Errors: %d.') % (
                    created, errors),
                'type': msg_type,
                'sticky': True,
            },
        }

    def action_open_summaries(self):
        """Open the list of all AI timesheet summaries."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Timesheet Summaries'),
            'res_model': 'timesheet.ai.summary',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_open_alerts(self):
        """Open summaries with overtime or underlog concerns."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Timesheet Alerts'),
            'res_model': 'timesheet.ai.summary',
            'view_mode': 'list,form',
            'domain': [
                '|',
                ('total_hours', '>', 45.0),
                '&',
                ('total_hours', '>', 0),
                ('total_hours', '<', 30.0),
            ],
            'target': 'current',
        }

    def action_open_config(self):
        """Open AI configuration settings."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Configuration'),
            'res_model': 'ollama.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_refresh(self):
        """Refresh the dashboard by reloading the form."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Timesheet Dashboard'),
            'res_model': 'timesheet.summary.dashboard',
            'view_mode': 'form',
            'target': 'current',
        }
