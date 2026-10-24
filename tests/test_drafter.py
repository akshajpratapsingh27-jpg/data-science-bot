"""Reply drafter tests - exactly three tests as required."""

import json
from datetime import UTC, datetime
from pathlib import Path

from dsbot.approvals import get_approval
from dsbot.audit import read_last
from dsbot.connectors.base import Email
from dsbot.connectors.mock_mail import MockMailProvider
from dsbot.email.drafter import EmailDrafter, ReplyVariant, approve_email_reply, post_check_draft
from dsbot.email.triage import EmailPriority, TriageCategory, TriageResult
from dsbot.llm import FakeLLM


def make_email() -> Email:
    return Email(
        id="draft-test-email",
        sender="requester@example.com",
        to=["analyst@company.example"],
        subject="Please review the analysis",
        body="Please review the analysis and let me know what is missing.",
        headers={"Message-ID": "<draft-test@example.com>"},
        attachments=[],
        received_at=datetime.now(UTC),
        thread_id="draft-thread",
    )


def make_classification() -> TriageResult:
    return TriageResult(
        email_id="draft-test-email",
        sender="requester@example.com",
        subject="Please review the analysis",
        category=TriageCategory.DATA_REQUEST,
        confidence=0.95,
        reason="The sender requests analysis review.",
        priority_score=20,
        priority=EmailPriority.LOW,
        needs_human_review=False,
        quarantined=False,
        action="none",
        llm_called=True,
        classification_source="llm",
        summary="The sender requests analysis review.",
        suggested_action="Prepare a reply for approval.",
    )


def canned_variants() -> str:
    return json.dumps(
        {
            "short": {
                "subject": "Re: Please review the analysis",
                "body": (
                    "Thanks for the request. I will review the analysis and follow up "
                    "with [PLACEHOLDER]."
                ),
            },
            "detailed": {
                "subject": "Re: Please review the analysis",
                "body": (
                    "Thank you for the context. I will review the analysis and identify "
                    "any missing information before responding."
                ),
            },
            "polite_decline": {
                "subject": "Re: Please review the analysis",
                "body": (
                    "Thank you for reaching out. I cannot review this immediately, but "
                    "[PLACEHOLDER] may be able to help."
                ),
            },
        }
    )


def test_three_variants_are_produced_and_stored_as_pending_approval(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    drafter = EmailDrafter(FakeLLM({"default": canned_variants()}))

    approval_id = drafter.draft_and_queue(make_email(), make_classification())
    approval = get_approval(approval_id)

    assert approval is not None
    assert approval["kind"] == "email_reply"
    assert approval["status"] == "pending"
    assert len(approval["payload"]["variants"]) == 3


def test_post_check_rejects_unknown_email_address():
    draft = ReplyVariant(
        variant="short",
        subject="Re: Please review the analysis",
        body="Please send the result to new-contact@evil.example.",
    )

    original = "From: requester@example.com\nBody: Please review the analysis."
    assert post_check_draft(draft, original) is False


def test_approve_writes_draft_file_and_audit_row(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    provider = MockMailProvider(
        mailbox_dir=tmp_path / "mailbox",
        draft_dir=tmp_path / "drafts",
    )
    drafter = EmailDrafter(FakeLLM({"default": canned_variants()}))
    approval_id = drafter.draft_and_queue(make_email(), make_classification())

    draft_path = approve_email_reply(approval_id, 1, provider)

    assert draft_path.exists()
    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    assert payload["status"] == "draft"
    assert payload["to"] == ["requester@example.com"]
    approval = get_approval(approval_id)
    assert approval is not None
    assert approval["status"] == "approved"
    assert any(
        entry["action"] == "email_reply_draft_created"
        and entry["details"]["approval_id"] == approval_id
        for entry in read_last(20)
    )
