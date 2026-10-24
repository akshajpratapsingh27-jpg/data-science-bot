"""Connector tests - exactly three tests as required."""

import json
from datetime import datetime, time
from pathlib import Path

from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.connectors.mock_mail import MockMailProvider


def test_mock_mailbox_returns_30_email_objects():
    emails = MockMailProvider().fetch_unread()

    assert len(emails) == 30
    assert all(email.id.startswith("mock-email-") for email in emails)


def test_find_slots_avoids_events_and_working_hour_boundaries():
    provider = MockCalendarProvider()
    existing = provider.get_events(
        datetime.min.replace(tzinfo=provider.timezone),
        datetime.max.replace(tzinfo=provider.timezone),
    )
    reference_day = existing[0].start.date()
    window_start = datetime.combine(reference_day, time(9), tzinfo=provider.timezone)
    window_end = datetime.combine(reference_day, time(17), tzinfo=provider.timezone)

    slots = provider.find_slots(30, window_start, window_end)

    assert slots
    for start, end in slots:
        assert time(9) <= start.time()
        assert end.time() <= time(17)
        assert all(event.end <= start or event.start >= end for event in existing)
        assert provider.is_free(start, end)


def test_create_draft_writes_a_file(tmp_path: Path):
    provider = MockMailProvider(draft_dir=tmp_path / "drafts")

    draft_path = provider.create_draft(
        ["recipient@example.com"],
        "Review requested",
        "Please review the attached analysis.",
        thread_id="thread-123",
    )

    assert draft_path.exists()
    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    assert payload["status"] == "draft"
    assert payload["to"] == ["recipient@example.com"]
    assert payload["subject"] == "Review requested"
