"""File-backed mock calendar provider with local scheduling guardrails."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dsbot.audit import log
from dsbot.config import Settings, load_settings
from dsbot.connectors.base import CalendarProvider, Event


class MockCalendarProvider(CalendarProvider):
    """Read and write calendar events while applying configured work hours."""

    def __init__(
        self,
        calendar_path: Path | str = "data/mock_calendar.json",
        settings: Settings | None = None,
    ) -> None:
        self.calendar_path = Path(calendar_path)
        self.calendar_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings or load_settings()
        self.timezone = ZoneInfo(self.settings.timezone)

    def _load_events(self) -> list[Event]:
        if not self.calendar_path.exists():
            return []
        raw = json.loads(self.calendar_path.read_text(encoding="utf-8"))
        records = raw.get("events", []) if isinstance(raw, dict) else raw
        if not isinstance(records, list):
            raise ValueError("Mock calendar must contain a list of events")
        return [self._normalize_event(Event.model_validate(record)) for record in records]

    def _save_events(self, events: list[Event]) -> None:
        payload = [event.model_dump(mode="json") for event in events]
        self.calendar_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    def _normalize_event(self, event: Event) -> Event:
        return event.model_copy(
            update={
                "start": self._normalize_datetime(event.start),
                "end": self._normalize_datetime(event.end),
                "timezone": self.settings.timezone,
            }
        )

    def _working_times(self) -> tuple[time, time]:
        start_text = self.settings.working_hours.get("start", "09:00")
        end_text = self.settings.working_hours.get("end", "17:00")
        return time.fromisoformat(start_text), time.fromisoformat(end_text)

    def _in_working_hours(self, start: datetime, end: datetime) -> bool:
        start = self._normalize_datetime(start)
        end = self._normalize_datetime(end)
        if start.date() != end.date() or start.weekday() >= 5 or end <= start:
            return False
        working_start, working_end = self._working_times()
        return working_start <= start.time() and end.time() <= working_end

    def get_events(self, start: datetime, end: datetime) -> list[Event]:
        """Return events overlapping ``[start, end)``."""

        normalized_start = self._normalize_datetime(start)
        normalized_end = self._normalize_datetime(end)
        return [
            event
            for event in self._load_events()
            if event.start < normalized_end and event.end > normalized_start
        ]

    def is_free(self, start: datetime, end: datetime) -> bool:
        """Return false outside work hours or when an event overlaps."""

        if not self._in_working_hours(start, end):
            return False
        return not self.get_events(start, end)

    def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        *,
        approval_id: int,
        attendees: list[str] | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> Event:
        """Persist a new event only when the requested interval is available."""

        self.require_approved_meeting(approval_id)
        normalized_start = self._normalize_datetime(start)
        normalized_end = self._normalize_datetime(end)
        if not self.is_free(normalized_start, normalized_end):
            raise ValueError("Requested event interval is outside work hours or conflicts")
        event = Event(
            id=uuid.uuid4().hex,
            title=title,
            start=normalized_start,
            end=normalized_end,
            attendees=attendees or [],
            location=location,
            description=description,
            timezone=self.settings.timezone,
        )
        events = self._load_events()
        events.append(event)
        self._save_events(events)
        log("bot", "calendar_event_created", approval_id=approval_id, event_id=event.id)
        return event

    def find_slots(
        self,
        duration_min: int,
        window_start: datetime,
        window_end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Find 30-minute-grid slots inside work hours with no conflicts."""

        if duration_min <= 0:
            raise ValueError("duration_min must be positive")
        start = self._normalize_datetime(window_start)
        end = self._normalize_datetime(window_end)
        if end <= start:
            return []

        slots: list[tuple[datetime, datetime]] = []
        current_day = start.date()
        last_day = end.date()
        working_start, working_end = self._working_times()
        while current_day <= last_day:
            if current_day.weekday() < 5:
                day_start = datetime.combine(current_day, working_start, tzinfo=self.timezone)
                day_end = datetime.combine(current_day, working_end, tzinfo=self.timezone)
                candidate = max(day_start, start)
                latest_end = min(day_end, end)
                while candidate + timedelta(minutes=duration_min) <= latest_end:
                    candidate_end = candidate + timedelta(minutes=duration_min)
                    if self.is_free(candidate, candidate_end):
                        slots.append((candidate, candidate_end))
                    candidate += timedelta(minutes=30)
            current_day += timedelta(days=1)
        return slots
