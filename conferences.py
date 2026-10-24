"""Conference deadline reminder generation."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ConferenceReminder(BaseModel):
    """One scheduled reminder item."""

    conference: str
    deadline_type: str
    deadline: date
    remind_on: date
    days_before: int


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _records(path: str | Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    values = raw.get("conferences", raw) if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise ValueError("Conference YAML must contain a list or a conferences list")
    return [dict(value) for value in values]


def generate_reminders(
    path: str | Path = "data/conferences.yaml",
    reference_date: date | None = None,
) -> list[ConferenceReminder]:
    """Generate 14-, 7-, and 1-day reminders for both configured deadlines."""

    del reference_date  # Reminders are generated for all configured dates, including past ones.
    reminders: list[ConferenceReminder] = []
    for record in _records(path):
        name = str(record.get("name", "[PLACEHOLDER]"))
        for deadline_type in ("abstract_deadline", "registration_deadline"):
            if not record.get(deadline_type):
                continue
            deadline = _as_date(record[deadline_type])
            for days_before in (14, 7, 1):
                reminders.append(
                    ConferenceReminder(
                        conference=name,
                        deadline_type=deadline_type,
                        deadline=deadline,
                        remind_on=deadline - timedelta(days=days_before),
                        days_before=days_before,
                    )
                )
    return sorted(reminders, key=lambda item: (item.remind_on, item.conference, item.deadline_type))


load_conferences = _records
generate_conference_reminders = generate_reminders
