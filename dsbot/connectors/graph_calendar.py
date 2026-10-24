"""Microsoft Graph calendar provider stub."""

from __future__ import annotations

from typing import Any

from dsbot.audit import log
from dsbot.connectors.base import CalendarProvider, Event


class GraphCalendarProvider(CalendarProvider):
    """Placeholder for a Microsoft Graph-backed calendar provider."""

    def get_events(self, start: Any, end: Any) -> list[Event]:
        # TODO: implement Graph event retrieval and normalization.
        raise NotImplementedError

    def is_free(self, start: Any, end: Any) -> bool:
        # TODO: use Graph calendarView/freeBusy data.
        raise NotImplementedError

    def create_event(
        self,
        title: str,
        start: Any,
        end: Any,
        *,
        approval_id: int,
        attendees: list[str] | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> Event:
        self.require_approved_meeting(approval_id)
        log("bot", "calendar_event_creation_authorized", approval_id=approval_id)
        # TODO: implement Graph event creation after the approval gate.
        raise NotImplementedError

    def find_slots(
        self,
        duration_min: int,
        window_start: Any,
        window_end: Any,
    ) -> list[tuple[Any, Any]]:
        # TODO: query Graph availability and apply local policy.
        raise NotImplementedError
