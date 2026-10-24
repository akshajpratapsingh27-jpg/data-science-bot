"""Daily operational digest generation."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from dsbot.audit import log
from dsbot.connectors.base import CalendarProvider
from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.db import get_db
from dsbot.learning.proposals import list_proposals


def _local_datetime(value: datetime) -> datetime:
    return value.astimezone() if value.tzinfo else value.astimezone()


def _is_local_day(value: str, day: date) -> bool:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _local_datetime(timestamp).date() == day


def _today_audit(action: str, day: date) -> list[dict[str, Any]]:
    rows = get_db().fetch_all(
        "SELECT ts, action, details_json FROM audit_log WHERE action = ? ORDER BY id DESC",
        (action,),
    )
    return [
        {**row, "details": json.loads(row["details_json"])}
        for row in rows
        if _is_local_day(row["ts"], day)
    ]


def _priority_emails(day: date) -> list[dict[str, Any]]:
    rows = get_db().fetch_all(
        "SELECT e.payload_json, c.classification_json, c.created_at "
        "FROM classifications c JOIN emails e ON e.id = c.email_id "
        "ORDER BY c.created_at DESC"
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_local_day(row["created_at"], day):
            continue
        classification = json.loads(row["classification_json"])
        if classification.get("category") in {"promotional", "suspicious"}:
            continue
        email = json.loads(row["payload_json"])
        latest.setdefault(
            str(classification.get("email_id", email.get("id", ""))),
            {"email": email, "classification": classification},
        )
    return sorted(
        latest.values(),
        key=lambda item: int(item["classification"].get("priority_score", 0)),
        reverse=True,
    )[:10]


def _flagged_emails(day: date) -> list[dict[str, Any]]:
    rows = get_db().fetch_all(
        "SELECT e.payload_json, c.classification_json, c.created_at "
        "FROM classifications c JOIN emails e ON e.id = c.email_id "
        "ORDER BY c.created_at DESC"
    )
    flagged: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_local_day(row["created_at"], day):
            continue
        classification = json.loads(row["classification_json"])
        if classification.get("category") != "suspicious":
            continue
        email = json.loads(row["payload_json"])
        email_id = str(classification.get("email_id", email.get("id", "")))
        flagged.setdefault(email_id, {"email": email, "classification": classification})
    return list(flagged.values())


def _schedule(
    provider: CalendarProvider,
    day: date,
) -> list[Any]:
    timezone = getattr(provider, "timezone", None)
    start = datetime.combine(day, time.min).astimezone(timezone)
    end = start + timedelta(days=1)
    return sorted(provider.get_events(start, end), key=lambda event: event.start)


def write_digest(
    output_dir: str | Path = "outputs/digest",
    calendar_provider: CalendarProvider | None = None,
    now: datetime | None = None,
) -> Path:
    """Write ``YYYY-MM-DD.md`` with the requested daily operational sections."""

    current = _local_datetime(now or datetime.now().astimezone())
    today = current.date()
    calendar = calendar_provider or MockCalendarProvider()
    events = _schedule(calendar, today)
    priority_emails = _priority_emails(today)
    flagged_emails = _flagged_emails(today)
    approvals = get_db().fetch_all(
        "SELECT id, kind, created_at FROM approvals "
        "WHERE status = 'pending' ORDER BY created_at"
    )
    report_files = sorted(
        (Path(output_dir).parent / "reports").glob("*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    quarantined = _today_audit("email_quarantined", today)
    quarantined_email_ids = {
        str(entry["details"].get("email_id"))
        for entry in quarantined
        if entry["details"].get("email_id")
    }
    errors = _today_audit("module_error", today)
    proposals = [proposal for proposal in list_proposals() if proposal.status == "pending"]

    sections = [
        f"# Daily digest — {today.isoformat()}",
        "## Today's schedule",
        *(
            [
                f"- {event.start.astimezone().strftime('%H:%M')}–"
                f"{event.end.astimezone().strftime('%H:%M')} — {event.title}"
                for event in events
            ]
            or ["- No scheduled events."]
        ),
        "## Priority emails",
        *(
            [
                f"- **{item['classification'].get('priority', 'unknown')}** "
                f"{item['email'].get('subject', '(no subject)')} "
                f"({item['email'].get('sender', 'unknown sender')})"
                for item in priority_emails
            ]
            or ["- No priority emails triaged today."]
        ),
        "## Flagged for review",
        *(
            [
                f"- **suspicious** {item['email'].get('subject', '(no subject)')} "
                f"({item['email'].get('sender', 'unknown sender')})"
                for item in flagged_emails
            ]
            or ["- No suspicious emails flagged."]
        ),
        "## Pending approvals",
        *(
            [f"- `{item['kind']}` — {item['id']}" for item in approvals]
            or ["- No pending approvals."]
        ),
        "## Reports ready",
        *([f"- [{path.name}](../reports/{path.name})" for path in report_files]
          or ["- No reports are ready."]),
        "## Quarantined promo count",
        f"- {len(quarantined_email_ids)}",
        "## Errors",
        *(
            [
                f"- {entry['details'].get('module', 'unknown')}: "
                f"{entry['details'].get('error', 'unknown error')}"
                for entry in errors
            ]
            or ["- No errors recorded today."]
        ),
        "## Learning proposals waiting",
        *(
            [f"- `{proposal.kind}` — {proposal.id}" for proposal in proposals]
            or ["- No learning proposals are waiting."]
        ),
    ]
    destination = Path(output_dir) / f"{today.isoformat()}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(sections) + "\n", encoding="utf-8")
    log("bot", "digest_generated", path=str(destination), date=today.isoformat())
    return destination


create_digest = write_digest
