# -*- coding: utf-8 -*-
import base64
import json
import logging
import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrApplicantInherit(models.Model):
    _inherit = ['hr.applicant', 'ollama.mixin']
    _name = 'hr.applicant'

    # ------------------------------------------------------------------
    # AI Analysis fields
    # ------------------------------------------------------------------
    ai_cv_score = fields.Integer(
        string='AI CV Score',
        default=0,
        help='AI-generated CV score from 0 to 100',
    )
    ai_cv_analysis = fields.Html(
        string='AI CV Analysis',
        readonly=True,
        help='Detailed AI analysis of the CV/resume',
    )
    ai_strengths = fields.Text(
        string='Strengths',
        readonly=True,
        help='Key strengths identified by AI',
    )
    ai_weaknesses = fields.Text(
        string='Weaknesses',
        readonly=True,
        help='Weaknesses or gaps identified by AI',
    )
    ai_interview_questions = fields.Text(
        string='Interview Questions',
        readonly=True,
        help='AI-generated interview questions',
    )
    ai_analysis_date = fields.Datetime(
        string='Analysis Date',
        readonly=True,
        help='Date and time of the last AI analysis',
    )

    # ------------------------------------------------------------------
    # Text extraction helpers
    # ------------------------------------------------------------------
    def _extract_text_from_attachments(self):
        """Extract text content from applicant attachments.

        Tries to decode base64 attachment data as plain text.
        For binary files (PDF, DOCX), returns the filename as context.
        """
        self.ensure_one()
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', 'hr.applicant'),
            ('res_id', '=', self.id),
        ])

        if not attachments:
            return ''

        text_parts = []
        for att in attachments:
            filename = att.name or 'unknown'
            text_parts.append(f"\n--- File: {filename} ---")

            if not att.datas:
                text_parts.append("[Empty attachment]")
                continue

            try:
                raw = base64.b64decode(att.datas)
            except Exception:
                text_parts.append(f"[Could not decode attachment: {filename}]")
                continue

            # Try plain text decoding first
            try:
                decoded_text = raw.decode('utf-8')
                text_parts.append(decoded_text)
                continue
            except (UnicodeDecodeError, ValueError):
                pass

            try:
                decoded_text = raw.decode('latin-1')
                text_parts.append(decoded_text)
                continue
            except (UnicodeDecodeError, ValueError):
                pass

            # For binary files (PDF, DOCX, etc.), extract printable strings
            # This is a best-effort approach without external libraries
            if filename.lower().endswith('.pdf'):
                text_parts.append(self._extract_text_from_pdf_bytes(raw, filename))
            else:
                # Extract any readable ASCII/UTF-8 strings from binary
                printable = self._extract_printable_strings(raw)
                if printable:
                    text_parts.append(printable)
                else:
                    text_parts.append(
                        f"[Binary file: {filename} - "
                        f"{len(raw)} bytes, content not directly readable]"
                    )

        return '\n'.join(text_parts)

    @staticmethod
    def _extract_text_from_pdf_bytes(raw_bytes, filename):
        """Extract readable text from raw PDF bytes without PyPDF2.

        Uses regex to find text between BT/ET markers and parenthesized strings
        common in PDF text streams.
        """
        text_chunks = []

        # Method 1: Extract text from PDF stream objects (Tj, TJ operators)
        try:
            # Find text in parentheses used by Tj operator
            paren_texts = re.findall(rb'\(([^)]{2,})\)', raw_bytes)
            for chunk in paren_texts:
                try:
                    decoded = chunk.decode('utf-8', errors='ignore')
                    # Filter out control sequences and very short fragments
                    cleaned = re.sub(r'[^\x20-\x7E\xC0-\xFF]', '', decoded)
                    if len(cleaned) > 2:
                        text_chunks.append(cleaned)
                except Exception:
                    continue
        except Exception:
            pass

        # Method 2: Extract readable ASCII strings (4+ chars)
        try:
            ascii_strings = re.findall(rb'[\x20-\x7E]{4,}', raw_bytes)
            for s in ascii_strings:
                decoded = s.decode('ascii', errors='ignore')
                # Skip PDF structural keywords
                skip_keywords = [
                    '/Type', '/Font', '/Page', 'endobj', 'endstream',
                    'xref', 'trailer', '/Length', '/Filter', '/Resources',
                    '/MediaBox', '/Contents', '/Parent', 'stream', 'obj',
                    '/Encoding', '/BaseFont', '/Subtype',
                ]
                if not any(kw in decoded for kw in skip_keywords):
                    if len(decoded.strip()) > 3:
                        text_chunks.append(decoded.strip())
        except Exception:
            pass

        if text_chunks:
            # Deduplicate while preserving order
            seen = set()
            unique = []
            for chunk in text_chunks:
                if chunk not in seen:
                    seen.add(chunk)
                    unique.append(chunk)
            return f"[PDF content from {filename}]:\n" + '\n'.join(unique[:200])

        return (
            f"[PDF file: {filename} - text extraction limited. "
            f"The AI will analyze based on available context.]"
        )

    @staticmethod
    def _extract_printable_strings(raw_bytes, min_length=4):
        """Extract printable ASCII strings from binary data."""
        strings = re.findall(
            rb'[\x20-\x7E]{%d,}' % min_length, raw_bytes
        )
        if not strings:
            return ''
        decoded = []
        for s in strings[:100]:
            try:
                decoded.append(s.decode('ascii', errors='ignore'))
            except Exception:
                continue
        return '\n'.join(decoded) if decoded else ''

    # ------------------------------------------------------------------
    # AI Actions
    # ------------------------------------------------------------------
    def action_analyze_cv(self):
        """Analyze the applicant's CV/resume using AI."""
        self.ensure_one()

        # 1. Extract CV text from attachments
        cv_text = self._extract_text_from_attachments()

        # 2. Fallback to applicant description if no attachments
        if not cv_text or cv_text.strip() in ('', '\n'):
            if self.description:
                cv_text = self.description
            else:
                raise UserError(_(
                    "No CV/resume found. Please attach a document to "
                    "this applicant or fill in the Application Summary."
                ))

        # 3. Build context
        job_name = self.job_id.name if self.job_id else 'Not specified'
        dept_name = self.department_id.name if self.department_id else 'Not specified'
        applicant_name = self.partner_name or self.partner_id.name if self.partner_id else 'Unknown'

        # 4. Build prompt
        system_prompt = (
            "You are an expert HR recruiter and CV analyst. "
            "You analyze CVs/resumes and provide structured assessments. "
            "Always respond with valid JSON only, no extra text."
        )

        prompt = f"""Analyze the following CV/resume for a job application.

Job Position: {job_name}
Department: {dept_name}
Applicant Name: {applicant_name}

CV/Resume Content:
{cv_text[:4000]}

Provide your analysis as a JSON object with exactly these keys:
{{
    "score": <integer from 0 to 100 indicating overall candidate fit>,
    "analysis": "<detailed paragraph about the candidate's profile, experience, and fit for the role>",
    "strengths": ["strength 1", "strength 2", "strength 3", "strength 4", "strength 5"],
    "weaknesses": ["weakness 1", "weakness 2", "weakness 3"],
    "interview_questions": [
        "question 1 specific to this candidate's background",
        "question 2 about their experience gaps",
        "question 3 about their technical skills",
        "question 4 about their soft skills",
        "question 5 about their career goals"
    ]
}}

Respond ONLY with the JSON object. No explanations, no markdown."""

        # 5. Call AI
        response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            max_tokens=2000,
            temperature=0.3,
            log_model='hr.applicant',
            log_res_id=self.id,
        )

        if not response:
            raise UserError(_(
                "AI did not return a response. "
                "Please check your AI configuration."
            ))

        # 6. Parse response
        data = self._parse_json_response(response)

        if not data or not isinstance(data, dict):
            # Store raw response as analysis fallback
            self.write({
                'ai_cv_analysis': f'<p>{response}</p>',
                'ai_analysis_date': fields.Datetime.now(),
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Partial Analysis'),
                    'message': _(
                        'AI response could not be fully parsed. '
                        'Raw analysis saved.'
                    ),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # 7. Write results
        score = data.get('score', 0)
        if isinstance(score, str):
            try:
                score = int(score)
            except (ValueError, TypeError):
                score = 0
        score = max(0, min(100, score))

        analysis = data.get('analysis', '')
        strengths = data.get('strengths', [])
        weaknesses = data.get('weaknesses', [])
        questions = data.get('interview_questions', [])

        # Format lists
        if isinstance(strengths, list):
            strengths_text = '\n'.join(f"- {s}" for s in strengths)
        else:
            strengths_text = str(strengths)

        if isinstance(weaknesses, list):
            weaknesses_text = '\n'.join(f"- {w}" for w in weaknesses)
        else:
            weaknesses_text = str(weaknesses)

        if isinstance(questions, list):
            questions_text = '\n'.join(
                f"{i+1}. {q}" for i, q in enumerate(questions)
            )
        else:
            questions_text = str(questions)

        # Format HTML analysis
        html_analysis = f"""
<div style="font-family: Arial, sans-serif;">
    <h3 style="color: #00BCD4;">CV Analysis Summary</h3>
    <p>{analysis}</p>
    <div style="margin-top: 10px; padding: 10px; background: #E0F7FA; border-radius: 5px;">
        <strong>Score:</strong> {score}/100 |
        <strong>Position:</strong> {job_name} |
        <strong>Department:</strong> {dept_name}
    </div>
</div>"""

        self.write({
            'ai_cv_score': score,
            'ai_cv_analysis': html_analysis,
            'ai_strengths': strengths_text,
            'ai_weaknesses': weaknesses_text,
            'ai_interview_questions': questions_text,
            'ai_analysis_date': fields.Datetime.now(),
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('CV Analysis Complete'),
                'message': _(
                    'Score: %d/100. Check the AI Analysis tab for details.'
                ) % score,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_generate_questions(self):
        """Generate targeted interview questions based on existing AI analysis."""
        self.ensure_one()

        # Build context from existing analysis
        context_parts = []
        if self.ai_cv_analysis:
            # Strip HTML for prompt
            analysis_text = re.sub(r'<[^>]+>', '', self.ai_cv_analysis or '')
            context_parts.append(f"Previous Analysis:\n{analysis_text}")
        if self.ai_strengths:
            context_parts.append(f"Strengths:\n{self.ai_strengths}")
        if self.ai_weaknesses:
            context_parts.append(f"Weaknesses:\n{self.ai_weaknesses}")

        if not context_parts:
            # No existing analysis: try to get CV text for context
            cv_text = self._extract_text_from_attachments()
            if not cv_text and self.description:
                cv_text = self.description
            if cv_text:
                context_parts.append(f"CV Content:\n{cv_text[:3000]}")
            else:
                raise UserError(_(
                    "No CV analysis or CV content found. "
                    "Please run 'Analyze CV' first or attach a document."
                ))

        job_name = self.job_id.name if self.job_id else 'Not specified'
        dept_name = self.department_id.name if self.department_id else 'Not specified'
        applicant_name = self.partner_name or (
            self.partner_id.name if self.partner_id else 'Unknown'
        )

        system_prompt = (
            "You are an expert HR interviewer. "
            "Generate targeted, insightful interview questions. "
            "Respond with valid JSON only."
        )

        prompt = f"""Based on the following candidate profile, generate 5 targeted interview questions.

Job Position: {job_name}
Department: {dept_name}
Candidate: {applicant_name}

{chr(10).join(context_parts)}

Generate 5 interview questions that:
1. Probe deeper into the candidate's claimed experience
2. Address identified weaknesses or gaps
3. Assess technical competency for the role
4. Evaluate cultural fit and soft skills
5. Explore career motivation and long-term goals

Respond as a JSON object:
{{
    "interview_questions": [
        "question 1",
        "question 2",
        "question 3",
        "question 4",
        "question 5"
    ]
}}

Respond ONLY with JSON. No extra text."""

        response = self._call_ollama_safe(
            prompt,
            system_prompt=system_prompt,
            max_tokens=1000,
            temperature=0.5,
            log_model='hr.applicant',
            log_res_id=self.id,
        )

        if not response:
            raise UserError(_(
                "AI did not return a response. "
                "Please check your AI configuration."
            ))

        data = self._parse_json_response(response)

        if data and isinstance(data, dict):
            questions = data.get('interview_questions', [])
            if isinstance(questions, list):
                questions_text = '\n'.join(
                    f"{i+1}. {q}" for i, q in enumerate(questions)
                )
            else:
                questions_text = str(questions)
        else:
            # Fallback: use raw response
            questions_text = response

        self.write({
            'ai_interview_questions': questions_text,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Interview Questions Generated'),
                'message': _('5 new interview questions have been generated.'),
                'type': 'success',
                'sticky': False,
            },
        }
