"""Email reply drafting for dsbot."""

import re

from pydantic import BaseModel, EmailStr

from dsbot.approval import ApprovalRequest, ApprovalType, get_approval_queue
from dsbot.audit import ActionType, get_audit_log
from dsbot.email.triage import Email, TriageResult
from dsbot.learning.memory import format_examples, get_examples
from dsbot.llm import LLM


class DraftReply(BaseModel):
    """A drafted email reply."""

    to_addrs: list[EmailStr]
    cc_addrs: list[EmailStr] = []
    subject: str
    body_text: str
    body_html: str | None = None
    in_reply_to: str  # Original email ID
    references: list[str] = []


class EmailDrafter:
    """Drafts email replies using LLM."""

    def __init__(self, llm: LLM):
        self.llm = llm

    def draft_reply(self, email: Email, triage: TriageResult, context: str = "") -> DraftReply:
        """Draft a reply to an email based on triage result."""
        prompt = self._build_prompt(email, triage, context)
        response = self.llm.complete(self._system_prompt(), prompt, json_mode=True)
        return self._parse_response(response, email, triage)

    def _system_prompt(self) -> str:
        return """You are an email reply drafter for a data scientist employee bot.
Write professional, concise replies based on the email category and context.

Guidelines:
- Be polite and professional
- Address the sender's main points
- If category is 'request', acknowledge and give timeline
- If category is 'inquiry', provide helpful answer or redirect
- If category is 'complaint', empathize and acknowledge
- Never commit to specific actions without human approval
- Keep replies under 200 words
- Use plain text format

Return ONLY valid JSON with keys: subject, body_text"""

    def _build_prompt(self, email: Email, triage: TriageResult, context: str) -> str:
        examples = format_examples(
            get_examples("email_reply", f"{email.subject}\n{email.body_text}", k=3)
        )
        return f"""Original email:
From: {email.from_addr}
Subject: {email.subject}
Body: {email.body_text[:1500]}

Triage:
Category: {triage.category.value}
Priority: {triage.priority.value}
Summary: {triage.summary}
Suggested action: {triage.suggested_action}

Additional context: {context}

{examples}

Draft a professional reply."""

    def _parse_response(self, response: str, email: Email, triage: TriageResult) -> DraftReply:
        """Parse LLM response into DraftReply."""
        import json

        try:
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)
        except (json.JSONDecodeError, AttributeError):
            data = {
                "subject": f"Re: {email.subject}",
                "body_text": (
                    "Thank you for your email. We have received it and will respond shortly."
                ),
            }

        subject = data.get("subject", f"Re: {email.subject}")
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"

        return DraftReply(
            to_addrs=[email.from_addr],
            cc_addrs=email.cc_addrs,
            subject=subject,
            body_text=data.get("body_text", "Thank you for your email. We will respond shortly."),
            in_reply_to=email.id,
            references=[email.id],
        )


def queue_draft_for_approval(
    draft: DraftReply,
    triage: TriageResult,
    original_email: Email,
    approval_queue=None,
    audit_log=None,
) -> ApprovalRequest:
    """Queue a drafted reply for human approval."""
    if approval_queue is None:
        approval_queue = get_approval_queue()
    if audit_log is None:
        audit_log = get_audit_log()

    request = ApprovalRequest(
        type=ApprovalType.EMAIL_SEND,
        payload=draft.model_dump(),
        metadata={
            "original_email_id": original_email.id,
            "original_from": original_email.from_addr,
            "original_subject": original_email.subject,
            "triage_category": triage.category.value,
            "triage_priority": triage.priority.value,
            "triage_summary": triage.summary,
        },
    )

    approval_queue.add(request)
    audit_log.log_action(
        ActionType.EMAIL_APPROVAL_QUEUED,
        actor="bot",
        details={"approval_id": request.id, "to": draft.to_addrs, "subject": draft.subject},
        correlation_id=request.id,
    )

    return request
