"""Conference management for dsbot."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from dsbot.approval import ApprovalRequest, ApprovalType, get_approval_queue
from dsbot.audit import ActionType, get_audit_log
from dsbot.meeting.scheduler import MeetingStatus


class Conference(BaseModel):
    """A conference event."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    description: str | None = None
    start_date: datetime
    end_date: datetime
    timezone: str = "UTC"
    location: str | None = None
    virtual_platform: str | None = None
    virtual_link: str | None = None
    organizer: EmailStr
    max_attendees: int | None = None
    status: MeetingStatus = MeetingStatus.PROPOSED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ConferenceAttendee(BaseModel):
    """An attendee registered for a conference."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    conference_id: str
    email: EmailStr
    name: str | None = None
    organization: str | None = None
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    checked_in: bool = False
    ticket_type: str = "standard"


class ConferenceManager:
    """Manages conferences and attendee registration."""

    def __init__(self):
        self.conferences: dict[str, Conference] = {}
        self.attendees: dict[str, list[ConferenceAttendee]] = {}

    def create_conference(
        self,
        name: str,
        organizer: EmailStr,
        start_date: datetime,
        end_date: datetime,
        description: str | None = None,
        location: str | None = None,
        virtual_platform: str | None = None,
        virtual_link: str | None = None,
        max_attendees: int | None = None,
        timezone: str = "UTC",
    ) -> Conference:
        """Create a conference (goes to approval queue)."""
        conference = Conference(
            name=name,
            organizer=organizer,
            start_date=start_date,
            end_date=end_date,
            description=description,
            location=location,
            virtual_platform=virtual_platform,
            virtual_link=virtual_link,
            max_attendees=max_attendees,
            timezone=timezone,
        )
        self.conferences[conference.id] = conference
        self.attendees[conference.id] = []
        return conference

    def register_attendee(
        self,
        conference_id: str,
        email: EmailStr,
        name: str | None = None,
        organization: str | None = None,
        ticket_type: str = "standard",
    ) -> ConferenceAttendee | None:
        """Register an attendee for a conference."""
        conference = self.conferences.get(conference_id)
        if not conference:
            return None

        if (
            conference.max_attendees
            and len(self.attendees[conference_id]) >= conference.max_attendees
        ):
            return None

        attendee = ConferenceAttendee(
            conference_id=conference_id,
            email=email,
            name=name,
            organization=organization,
            ticket_type=ticket_type,
        )
        self.attendees[conference_id].append(attendee)
        return attendee

    def check_in(self, conference_id: str, email: EmailStr) -> bool:
        """Check in an attendee."""
        for attendee in self.attendees.get(conference_id, []):
            if attendee.email == email:
                attendee.checked_in = True
                return True
        return False

    def get_conference(self, conference_id: str) -> Conference | None:
        return self.conferences.get(conference_id)

    def list_conferences(self) -> list[Conference]:
        return list(self.conferences.values())

    def get_attendees(self, conference_id: str) -> list[ConferenceAttendee]:
        return self.attendees.get(conference_id, [])

    def get_attendee_count(self, conference_id: str) -> int:
        return len(self.attendees.get(conference_id, []))


def queue_conference_for_approval(
    conference: Conference,
    approval_queue=None,
    audit_log=None,
) -> ApprovalRequest:
    """Queue a conference for human approval."""
    if approval_queue is None:
        approval_queue = get_approval_queue()
    if audit_log is None:
        audit_log = get_audit_log()

    request = ApprovalRequest(
        type=ApprovalType.CONFERENCE_BOOK,
        payload=conference.model_dump(),
        metadata={
            "conference_name": conference.name,
            "organizer": conference.organizer,
            "dates": f"{conference.start_date.date()} to {conference.end_date.date()}",
        },
    )

    approval_queue.add(request)
    audit_log.log_action(
        ActionType.MEETING_CONFERENCE_CREATE,
        actor="bot",
        details={"approval_id": request.id, "conference": conference.name},
        correlation_id=request.id,
    )

    return request
