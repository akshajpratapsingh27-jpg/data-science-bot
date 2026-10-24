"""Meeting workflow tests - exactly three tests as required."""

import json
from datetime import UTC, datetime

from dsbot.connectors.base import Email
from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.db import get_db
from dsbot.llm import FakeLLM
from dsbot.meetings.conferences import generate_reminders
from dsbot.meetings.minutes import parse_minutes
from dsbot.meetings.scheduler import MeetingScheduler


def _email(body: str = "Please schedule a client review.") -> Email:
    return Email(
        id="meeting-email-001",
        sender="requester@example.com",
        to=["owner@example.com"],
        subject="Client review meeting",
        body=body,
        received_at=datetime(2026, 10, 24, 12, tzinfo=UTC),
    )


def _meeting_llm() -> FakeLLM:
    return FakeLLM(
        {
            "default": json.dumps(
                {
                    "duration_min": 60,
                    "topic": "Client review",
                    "participants": ["requester@example.com", "owner@example.com"],
                    "proposed_times": [
                        "2026-10-26T09:00:00-04:00",
                        "2026-10-28T17:00:00-04:00",
                    ],
                    "timezone": "Europe/London",
                }
            )
        }
    )


def test_proposed_slots_are_free_inside_hours_and_timezone_converted(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    calendar = MockCalendarProvider(tmp_path / "calendar.json")
    scheduler = MeetingScheduler(calendar, _meeting_llm())

    proposal = scheduler.propose_from_email(_email(), classification="meeting_request")

    assert len(proposal.slots) == 3
    assert all(slot.timezone == "Europe/London" for slot in proposal.slots)
    for slot in proposal.slots:
        start = slot.start.astimezone(calendar.timezone)
        end = slot.end.astimezone(calendar.timezone)
        assert calendar.is_free(start, end)
        assert 9 <= start.hour or (start.hour == 9 and start.minute >= 0)
        assert end.hour < 17 or (end.hour == 17 and end.minute == 0)
    assert proposal.slots[0].start.hour == 13


def test_higher_priority_conflict_queues_reschedule_without_moving_event(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(
        json.dumps(
            [
                {
                    "id": "low-priority-event",
                    "title": "Optional sync",
                    "start": "2026-10-26T09:00:00-04:00",
                    "end": "2026-10-26T10:00:00-04:00",
                    "attendees": [],
                    "timezone": "America/New_York",
                    "priority": "optional",
                }
            ]
        ),
        encoding="utf-8",
    )
    calendar = MockCalendarProvider(calendar_path)
    scheduler = MeetingScheduler(calendar, _meeting_llm())

    proposal = scheduler.propose_from_email(
        _email("Please schedule this client meeting at 09:00."),
        classification="meeting_request",
        priority_tier="client",
    )
    pending = get_db().fetch_all(
        "SELECT kind, payload_json FROM approvals WHERE status = 'pending'"
    )
    kinds = [row["kind"] for row in pending]

    assert proposal.reschedule_approval_ids
    assert "meeting_reschedule" in kinds
    events = calendar.get_events(
        datetime(2026, 10, 26, 8, tzinfo=calendar.timezone),
        datetime(2026, 10, 26, 18, tzinfo=calendar.timezone),
    )
    assert len(events) == 1
    assert events[0].id == "low-priority-event"


def test_conference_reminders_and_unknown_transcript_owner(tmp_path):
    conference_file = tmp_path / "conferences.yaml"
    conference_file.write_text(
        "conferences:\n"
        "  - name: ExampleConf\n"
        "    dates: {start: '2027-04-10', end: '2027-04-12'}\n"
        "    abstract_deadline: '2027-01-10'\n"
        "    registration_deadline: '2027-03-20'\n",
        encoding="utf-8",
    )
    reminders = generate_reminders(conference_file)
    minutes = parse_minutes(
        "Alice said the project is ready.",
        FakeLLM(
            {
                "default": json.dumps(
                    {
                        "minutes": "The project is ready.",
                        "action_items": [
                            {"task": "Send the report", "owner": "Bob", "due_date": "2027-01-05"}
                        ],
                    }
                )
            }
        ),
    )

    assert {item.days_before for item in reminders} == {14, 7, 1}
    assert len(reminders) == 6
    assert minutes.action_items[0].owner == "UNASSIGNED"
