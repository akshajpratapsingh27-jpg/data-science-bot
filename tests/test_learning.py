"""Learning tests - exactly three tests as required."""

import json
from datetime import UTC, datetime

from dsbot.connectors.base import Email
from dsbot.connectors.mock_mail import MockMailProvider
from dsbot.email.drafter import EmailDrafter, approve_email_reply
from dsbot.email.triage import EmailPriority, TriageCategory, TriageResult
from dsbot.learning.proposals import approve_proposal, create_proposal
from dsbot.learning.versions import rollback
from dsbot.llm import FakeLLM


def _email(email_id: str = "learning-email") -> Email:
    return Email(
        id=email_id,
        sender="requester@example.com",
        to=["analyst@company.example"],
        subject="Please review the analysis",
        body="Please review the analysis and identify anything missing.",
        headers={"Message-ID": "<learning@example.com>"},
        attachments=[],
        received_at=datetime.now(UTC),
        thread_id="learning-thread",
    )


def _classification() -> TriageResult:
    return TriageResult(
        email_id="learning-email",
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


def _variants() -> str:
    return json.dumps(
        {
            "short": {
                "subject": "Re: Please review the analysis",
                "body": (
                    "Thanks for the request. I will review it and follow up with "
                    "[PLACEHOLDER]."
                ),
            },
            "detailed": {
                "subject": "Re: Please review the analysis",
                "body": "Thank you for the context. I will review the analysis before responding.",
            },
            "polite_decline": {
                "subject": "Re: Please review the analysis",
                "body": "Thank you for reaching out. [PLACEHOLDER] may be able to help.",
            },
        }
    )


def test_approved_human_edit_is_retrieved_for_a_similar_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    provider = MockMailProvider(draft_dir=tmp_path / "drafts")
    first_llm = FakeLLM({"default": _variants()})
    drafter = EmailDrafter(first_llm)
    approval_id = drafter.draft_and_queue(_email(), _classification())
    edited = "Thanks for the request. I will review this carefully and respond with next steps."

    approve_email_reply(approval_id, 1, provider, final_text=edited)

    second_llm = FakeLLM({"default": _variants()})
    EmailDrafter(second_llm).generate_variants(_email("similar-learning-email"), _classification())
    prompt = second_llm.calls[0]["user"]
    assert "PAST HUMAN-APPROVED EXAMPLES" in prompt
    assert edited in prompt


def test_proposal_that_lowers_golden_recall_is_auto_rejected_and_not_applied(tmp_path):
    target = tmp_path / "config" / "learned_style.md"
    before_text = "original style\n"
    target.parent.mkdir(parents=True)
    target.write_text(before_text, encoding="utf-8")
    proposal = create_proposal(
        kind="style",
        diff={"files": {"config/learned_style.md": "unsafe style\n"}},
        expected_effect="unsafe",
        golden_before={"important_recall": 1.0, "injection_resistance": True},
        golden_after={"important_recall": 0.5, "injection_resistance": True},
        proposals_dir=tmp_path / "proposals",
    )

    assert proposal.status == "rejected"
    assert target.read_text(encoding="utf-8") == before_text


def test_applied_proposal_snapshots_and_rollback_restores_config_exactly(tmp_path):
    config_path = tmp_path / "config" / "settings.yaml"
    config_path.parent.mkdir(parents=True)
    original = "triage_weights:\n  vip_sender: 40\n"
    config_path.write_text(original, encoding="utf-8")
    proposal = create_proposal(
        kind="triage_weights",
        diff={"settings": {"triage_weights": {"vip_sender": 99}}},
        expected_effect="Increase VIP weight",
        golden_before={"important_recall": 1.0, "injection_resistance": True},
        golden_after={"important_recall": 1.0, "injection_resistance": True},
        proposals_dir=tmp_path / "proposals",
    )

    applied = approve_proposal(
        proposal.id,
        proposals_dir=tmp_path / "proposals",
        versions_dir=tmp_path / "versions",
        project_root=tmp_path,
    )
    assert applied.status == "approved"
    assert applied.version is not None
    assert config_path.read_text(encoding="utf-8") != original

    rollback(applied.version, tmp_path / "versions", tmp_path)
    assert config_path.read_text(encoding="utf-8") == original
