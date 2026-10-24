"""Abstract connector contracts and shared provider models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from dsbot.audit import log
from dsbot.db import get_db


class Email(BaseModel):
    """A normalized email message returned by a mail provider."""

    id: str
    sender: str
    to: list[str]
    subject: str
    body: str
    headers: dict[str, str] = Field(default_factory=dict)
    attachments: list[str] = Field(default_factory=list)
    received_at: datetime
    thread_id: str | None = None

    @property
    def from_addr(self) -> str:
        """Legacy alias used by the original email module."""

        return self.sender

    @property
    def to_addrs(self) -> list[str]:
        """Legacy alias used by the original email module."""

        return self.to

    @property
    def cc_addrs(self) -> list[str]:
        """Legacy compatibility field; normalized connectors currently omit CC."""

        return []

    @property
    def body_text(self) -> str:
        """Legacy alias used by the original email module."""

        return self.body


class Event(BaseModel):
    """A normalized calendar event."""

    id: str
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = Field(default_factory=list)
    location: str | None = None
    description: str | None = None
    timezone: str = "UTC"
    priority: str = "internal_sync"


class MailProvider(ABC):
    """Interface implemented by mail providers."""

    # Mail providers are draft-only by design; this interface intentionally has no send method.

    @abstractmethod
    def fetch_unread(self) -> list[Email]:
        """Fetch unread messages without sending or deleting anything."""

    @abstractmethod
    def mark(self, email_id: str, label: str) -> None:
        """Apply a label to an email."""

    @abstractmethod
    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        thread_id: str | None = None,
        cc: list[str] | None = None,
    ) -> Any:
        """Create a draft for human review; never send it."""


class CalendarProvider(ABC):
    """Interface implemented by calendar providers."""

    @abstractmethod
    def get_events(self, start: datetime, end: datetime) -> list[Event]:
        """Return events overlapping the requested window."""

    @abstractmethod
    def is_free(self, start: datetime, end: datetime) -> bool:
        """Return whether the requested interval has no conflicts."""

    @abstractmethod
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
        """Create a calendar event after a human approval check."""

    @staticmethod
    def require_approved_meeting(approval_id: int) -> dict[str, Any]:
        """Require an approved meeting_reply using the approval table's integer row ID."""

        row = get_db().fetch_one(
            "SELECT rowid AS approval_id, kind, status FROM approvals WHERE rowid = ?",
            (approval_id,),
        )
        if row is None or row["kind"] != "meeting_reply" or row["status"] != "approved":
            log(
                "bot",
                "calendar_event_creation_refused",
                approval_id=approval_id,
                kind=row["kind"] if row else None,
                status=row["status"] if row else None,
            )
            raise PermissionError(
                "Calendar event creation requires an approved meeting_reply"
            )
        return row

    @abstractmethod
    def find_slots(
        self,
        duration_min: int,
        window_start: datetime,
        window_end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Return conflict-free slots inside the requested window."""
