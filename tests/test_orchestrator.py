"""Orchestration tests - exactly three tests as required."""

import json
from datetime import UTC, datetime

import pandas as pd

from dsbot.connectors.base import Email
from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.db import get_db, json_dumps
from dsbot.llm import FakeLLM
from dsbot.orchestrator import run_cycle
from dsbot.selfreview import write_self_review


class _MailProvider:
    def __init__(self, emails):
        self.emails = emails

    def fetch_unread(self):
        return self.emails

    def mark(self, email_id, label):
        return None

    def create_draft(self, to, subject, body, thread_id=None, cc=None):
        return None


def _email(email_id, subject, body, attachments=None):
    return Email(
        id=email_id,
        sender="requester@example.com",
        to=["analyst@example.com"],
        subject=subject,
        body=body,
        attachments=attachments or [],
        received_at=datetime(2026, 10, 24, 9, tzinfo=UTC),
    )


def _llm():
    def respond(system, user):
        if "You classify email" in system:
            text = user.casefold()
            category = (
                "meeting_request"
                if "meeting" in text
                else "data_request"
                if "dataset" in text
                else "urgent_action"
            )
            return json.dumps(
                {"category": category, "confidence": 0.95, "reason": "Request received."}
            )
        if "Extract meeting requests" in system:
            return json.dumps(
                {
                    "duration_min": 60,
                    "topic": "Project review",
                    "participants": ["requester@example.com"],
                    "proposed_times": ["2026-10-26T09:00:00+00:00"],
                    "timezone": "UTC",
                }
            )
        if "Return strict JSON only with keys short, detailed, and polite_decline" in system:
            return json.dumps(
                {
                    name: {
                        "subject": "Re: Request",
                        "body": "Thanks for reaching out. [PLACEHOLDER]",
                    }
                    for name in ("short", "detailed", "polite_decline")
                }
            )
        return "The dataset is ready for human review."

    return FakeLLM(respond)


def _empty_conferences(path):
    path.write_text("conferences: []\n", encoding="utf-8")
    return path


def test_run_cycle_creates_email_meeting_data_approvals_and_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    csv_path = tmp_path / "dataset.csv"
    pd.DataFrame(
        {
            "sale_id": ["S-001", "S-002"],
            "sale_date": ["2026-10-01", "2026-10-02"],
            "category": ["retail", "online"],
            "region": [" east ", "west"],
            "amount": [10, 20],
        }
    ).to_csv(csv_path, index=False)
    emails = [
        _email(
            "data-email",
            "Dataset analysis",
            "Please analyze the attached dataset.",
            [str(csv_path)],
        ),
        _email("meeting-email", "Meeting request", "Please schedule a meeting."),
        _email("reply-email", "Urgent status", "Please provide a status update."),
    ]
    conference_file = _empty_conferences(tmp_path / "conferences.yaml")

    results = run_cycle(
        _MailProvider(emails),
        MockCalendarProvider(tmp_path / "calendar.json"),
        _llm(),
        output_root=tmp_path / "outputs",
        conference_file=conference_file,
        now=datetime(2026, 10, 26, 10, tzinfo=UTC),
    )
    approvals = get_db().fetch_all("SELECT kind FROM approvals")

    assert {row["kind"] for row in approvals} >= {
        "email_reply",
        "meeting_reply",
        "data_report",
    }
    assert results["errors"] == []
    assert (tmp_path / "outputs" / "digest" / "2026-10-26.md").exists()
    assert list((tmp_path / "outputs" / "excel").glob("*.xlsx"))
    assert list((tmp_path / "outputs" / "reports").glob("*.md"))


def test_module_failure_is_logged_and_other_cycle_modules_complete(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    conference_file = _empty_conferences(tmp_path / "conferences.yaml")

    def fail_triage(emails, llm):
        raise RuntimeError("triage unavailable")

    monkeypatch.setattr("dsbot.orchestrator._triage_emails", fail_triage)
    results = run_cycle(
        _MailProvider([]),
        MockCalendarProvider(tmp_path / "calendar.json"),
        _llm(),
        output_root=tmp_path / "outputs",
        conference_file=conference_file,
        now=datetime(2026, 10, 26, 10, tzinfo=UTC),
    )
    error = get_db().fetch_one(
        "SELECT details_json FROM audit_log WHERE action = 'module_error'"
    )

    assert results["fetch_mail"] == []
    assert results["triage"] is None
    assert results["draft_replies"] == []
    assert results["meeting_proposals"] == []
    assert results["conference_reminders"] == []
    assert json.loads(error["details_json"])["error"] == "triage unavailable"
    assert (tmp_path / "outputs" / "digest" / "2026-10-26.md").exists()


def test_weekly_self_review_includes_acceptance_trend_and_golden_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    now = datetime(2026, 10, 26, 10, tzinfo=UTC)
    store = get_db()
    store.execute(
        "INSERT INTO approvals (id, kind, payload_json, status, created_at) "
        "VALUES (?, 'email_reply', '{}', 'approved', ?)",
        ("approved-reply", now.isoformat()),
    )
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(
        json.dumps(
            [
                {"id": "important-email", "important": True, "promotional": False},
                {"id": "promo-email", "important": False, "promotional": True},
            ]
        ),
        encoding="utf-8",
    )
    store.execute(
        "INSERT INTO classifications (email_id, classification_json, created_at) "
        "VALUES (?, ?, ?)",
        (
            "important-email",
            json_dumps({"category": "urgent_action", "priority": "high"}),
            now.isoformat(),
        ),
    )
    store.execute(
        "INSERT INTO classifications (email_id, classification_json, created_at) "
        "VALUES (?, ?, ?)",
        (
            "promo-email",
            json_dumps({"category": "promotional", "priority": "low"}),
            now.isoformat(),
        ),
    )

    report_path = write_self_review(
        tmp_path / "reviews",
        now=now,
        golden_path=golden_path,
    )
    report = report_path.read_text(encoding="utf-8")

    assert "Draft acceptance rate trend" in report
    assert "Golden-set scores" in report
    assert '"important_recall": 1.0' in report
    assert '"promo_precision": 1.0' in report
