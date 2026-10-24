"""Hardening tests - exactly three tests as required."""

import json
from datetime import UTC, datetime

from dsbot.audit import read_last
from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.connectors.mock_mail import MockMailProvider
from dsbot.llm import FakeLLM
from dsbot.orchestrator import run_cycle


def test_kill_switch_stops_cycle_and_writes_audit_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    kill_switch = tmp_path / "data" / "KILL"
    kill_switch.parent.mkdir()
    kill_switch.write_text("stop", encoding="utf-8")

    class MailProvider:
        fetches = 0

        def fetch_unread(self):
            self.fetches += 1
            return []

    mail = MailProvider()
    result = run_cycle(mail_provider=mail, kill_switch_path=kill_switch)
    logs = read_last(10)

    assert result == {"stopped": True, "reason": "kill_switch"}
    assert mail.fetches == 0
    assert any(entry["action"] == "kill_switch_activated" for entry in logs)


def test_pii_is_redacted_from_prompts_captured_by_fake_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    email_address = "private.person@example.org"
    phone_number = "415-555-2671"
    identifier = "123456789012"
    llm = FakeLLM(
        lambda system, user: (
            "Local restoration: [USER_EMAIL_0001] "
            "[USER_PHONE_0002] [USER_ID_0003]"
        )
    )
    original = f"Contact {email_address} or {phone_number}; record {identifier}."

    response = llm.complete("system", original)
    captured = llm.calls[0]["user"]

    assert email_address not in captured
    assert phone_number not in captured
    assert identifier not in captured
    assert "[USER_EMAIL_0001]" in captured
    assert "[USER_PHONE_0002]" in captured
    assert "[USER_ID_0003]" in captured
    assert email_address in response
    assert phone_number in response
    assert identifier in response


def test_mock_mailbox_cycle_never_sends_or_books_without_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    mailbox = tmp_path / "mock_mailbox"
    mailbox.mkdir()
    (mailbox / "meeting.json").write_text(
        json.dumps(
            {
                "id": "meeting-001",
                "sender": "new.requester@outside.test",
                "to": ["analyst@company.example"],
                "subject": "Project review meeting",
                "body": "Please schedule a meeting about the project.",
                "headers": {},
                "attachments": [],
                "received_at": "2026-10-24T10:00:00+00:00",
                "thread_id": "meeting-thread-001",
                "labels": [],
            }
        ),
        encoding="utf-8",
    )
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text("[]", encoding="utf-8")
    conferences = tmp_path / "conferences.yaml"
    conferences.write_text("conferences: []\n", encoding="utf-8")

    class ObservedMockMailProvider(MockMailProvider):
        sent = 0

        def send(self, email):
            self.sent += 1

    mail = ObservedMockMailProvider(mailbox, tmp_path / "drafts")
    calendar = MockCalendarProvider(calendar_path)

    def answer(system, user):
        if "You classify email" in system:
            return json.dumps(
                {
                    "category": "meeting_request",
                    "confidence": 0.95,
                    "reason": "A meeting was requested.",
                }
            )
        if "Extract meeting requests" in system:
            return json.dumps(
                {
                    "duration_min": 30,
                    "topic": "Project review",
                    "participants": ["new.requester@outside.test"],
                    "proposed_times": ["2026-10-26T10:00:00-04:00"],
                    "timezone": "America/New_York",
                }
            )
        if "Return strict JSON only with keys short, detailed, and polite_decline" in system:
            return json.dumps(
                {
                    variant: {
                        "subject": "Re: Project review",
                        "body": "Thanks for reaching out. [PLACEHOLDER]",
                    }
                    for variant in ("short", "detailed", "polite_decline")
                }
            )
        return "No additional facts."

    result = run_cycle(
        mail,
        calendar,
        FakeLLM(answer),
        output_root=tmp_path / "outputs",
        conference_file=conferences,
        kill_switch_path=tmp_path / "data" / "KILL",
        now=datetime(2026, 10, 24, 12, tzinfo=UTC),
    )
    from dsbot.approvals import list_pending

    approvals = list_pending()

    assert result["errors"] == []
    assert mail.sent == 0
    assert calendar_path.read_text(encoding="utf-8") == "[]"
    assert any(item["kind"] == "meeting_reply" for item in approvals)
    email_approval = next(item for item in approvals if item["kind"] == "email_reply")
    assert "NEW EXTERNAL RECIPIENT" in email_approval["payload"]["security_notice"]
    assert (tmp_path / "outputs" / "digest" / "2026-10-24.md").exists()
