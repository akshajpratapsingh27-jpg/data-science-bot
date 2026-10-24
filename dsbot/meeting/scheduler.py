"""Meeting scheduling for dsbot."""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class MeetingStatus(str, Enum):
    """Meeting status."""

    PROPOSED = "proposed"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class Meeting(BaseModel):
    """A meeting."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    description: str | None = None
    organizer: EmailStr
    attendees: list[EmailStr] = []
    start_time: datetime
    end_time: datetime
    timezone: str = "UTC"
    location: str | None = None
    virtual_link: str | None = None
    status: MeetingStatus = MeetingStatus.PROPOSED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TimeSlot(BaseModel):
    """An available time slot."""

    start: datetime
    end: datetime
    timezone: str = "UTC"


class MeetingScheduler:
    """Schedules meetings and finds available slots."""

    def __init__(self, calendar_connector: Any = None):
        self.calendar = calendar_connector

    def propose_meeting(
        self,
        title: str,
        organizer: EmailStr,
        attendees: list[EmailStr],
        duration_minutes: int = 60,
        preferred_times: list[TimeSlot] | None = None,
        timezone: str = "UTC",
    ) -> Meeting:
        """Propose a meeting (goes to approval queue)."""
        # If no preferred times, suggest next business day at 10 AM
        if not preferred_times:
            start = self._next_business_day(
                datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
            )
            preferred_times = [
                TimeSlot(start=start, end=start + timedelta(minutes=duration_minutes))
            ]

        # Use first preferred time for now
        slot = preferred_times[0]
        meeting = Meeting(
            title=title,
            organizer=organizer,
            attendees=attendees,
            start_time=slot.start,
            end_time=slot.end,
            timezone=timezone,
        )
        return meeting

    def find_available_slots(
        self,
        attendees: list[EmailStr],
        duration_minutes: int,
        date_range: tuple[datetime, datetime],
        timezone: str = "UTC",
    ) -> list[TimeSlot]:
        """Find available time slots for attendees."""
        # In real implementation, query calendar for each attendee
        # For now, return business hours slots
        slots = []
        current = date_range[0]
        end = date_range[1]

        while current < end:
            if current.weekday() < 5:  # Monday-Friday
                for hour in [9, 10, 11, 14, 15, 16]:  # Business hours
                    slot_start = current.replace(hour=hour, minute=0, second=0, microsecond=0)
                    slot_end = slot_start + timedelta(minutes=duration_minutes)
                    if slot_end <= end:
                        slots.append(TimeSlot(start=slot_start, end=slot_end, timezone=timezone))
            current += timedelta(days=1)

        return slots[:20]  # Limit results

    def _next_business_day(self, dt: datetime) -> datetime:
        """Get next business day."""
        while dt.weekday() >= 5:  # Saturday=5, Sunday=6
            dt += timedelta(days=1)
        return dt


class CalendarConnector(ABC):
    """Abstract interface for calendar connectors."""

    @abstractmethod
    def create_event(self, meeting: Meeting) -> str:
        """Create calendar event, return event ID."""
        ...

    @abstractmethod
    def update_event(self, event_id: str, meeting: Meeting) -> bool:
        """Update calendar event."""
        ...

    @abstractmethod
    def delete_event(self, event_id: str) -> bool:
        """Delete calendar event."""
        ...

    @abstractmethod
    def get_free_busy(
        self,
        emails: list[EmailStr],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[TimeSlot]]:
        """Get free/busy info for attendees."""
        ...


class MockCalendarConnector(CalendarConnector):
    """Mock calendar connector for testing."""

    def __init__(self):
        self.events: dict[str, Meeting] = {}

    def create_event(self, meeting: Meeting) -> str:
        self.events[meeting.id] = meeting
        return meeting.id

    def update_event(self, event_id: str, meeting: Meeting) -> bool:
        if event_id in self.events:
            self.events[event_id] = meeting
            return True
        return False

    def delete_event(self, event_id: str) -> bool:
        if event_id in self.events:
            del self.events[event_id]
            return True
        return False

    def get_free_busy(
        self,
        emails: list[EmailStr],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[TimeSlot]]:
        # Return all business hours as free
        return {email: [] for email in emails}


class GoogleCalendarConnector(CalendarConnector):
    """Google Calendar connector (placeholder)."""

    def __init__(self, credentials_path: str):
        self.credentials_path = credentials_path

    def create_event(self, meeting: Meeting) -> str:
        raise NotImplementedError("GoogleCalendarConnector not implemented")

    def update_event(self, event_id: str, meeting: Meeting) -> bool:
        raise NotImplementedError("GoogleCalendarConnector not implemented")

    def delete_event(self, event_id: str) -> bool:
        raise NotImplementedError("GoogleCalendarConnector not implemented")

    def get_free_busy(
        self,
        emails: list[EmailStr],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[TimeSlot]]:
        raise NotImplementedError("GoogleCalendarConnector not implemented")


class OutlookCalendarConnector(CalendarConnector):
    """Outlook Calendar connector (placeholder)."""

    def __init__(self, client_id: str, client_secret: str, tenant_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id

    def create_event(self, meeting: Meeting) -> str:
        raise NotImplementedError("OutlookCalendarConnector not implemented")

    def update_event(self, event_id: str, meeting: Meeting) -> bool:
        raise NotImplementedError("OutlookCalendarConnector not implemented")

    def delete_event(self, event_id: str) -> bool:
        raise NotImplementedError("OutlookCalendarConnector not implemented")

    def get_free_busy(
        self, emails: list[EmailStr], start: datetime, end: datetime
    ) -> dict[str, list[TimeSlot]]:
        raise NotImplementedError("OutlookCalendarConnector not implemented")
