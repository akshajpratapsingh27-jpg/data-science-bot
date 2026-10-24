"""CLI entry point for dsbot."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from pydantic import EmailStr

from dsbot.approval import get_approval_queue
from dsbot.approvals import approve as approve_sqlite
from dsbot.approvals import get_approval
from dsbot.approvals import list_pending as list_pending_approvals
from dsbot.approvals import reject as reject_approval
from dsbot.audit import get_audit_log
from dsbot.config import load_settings
from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.connectors.mock_mail import MockMailProvider
from dsbot.data.analysis import analyze
from dsbot.data.excel import build_workbook
from dsbot.data.pipeline import run as run_data_pipeline
from dsbot.data.report import build_report
from dsbot.digest import write_digest
from dsbot.email.draft import EmailDrafter, queue_draft_for_approval
from dsbot.email.drafter import approve_email_reply
from dsbot.email.triage import Email, EmailTriage
from dsbot.learning.feedback import mark_email, record_meeting_slot_choice
from dsbot.learning.proposals import (
    approve_proposal,
    list_proposals,
    rollback_proposal,
)
from dsbot.learning.style_learner import learn_style_proposal
from dsbot.learning.tuner import propose_meeting_preferences, propose_priority_tuning
from dsbot.llm import LLM, FakeLLM, get_llm
from dsbot.meeting.conference import ConferenceManager, queue_conference_for_approval
from dsbot.meeting.scheduler import MeetingScheduler, MockCalendarConnector, TimeSlot
from dsbot.meetings.conferences import generate_reminders
from dsbot.meetings.minutes import generate_minutes
from dsbot.meetings.prep import generate_prep_brief
from dsbot.meetings.scheduler import MeetingScheduler as EmailMeetingScheduler
from dsbot.meetings.scheduler import approve_meeting_reply
from dsbot.orchestrator import run_cycle
from dsbot.scheduler import start_scheduler

app = typer.Typer(name="dsbot", help="Data Scientist Employee Bot")
data_app = typer.Typer(name="data", help="Run data processing workflows")
approvals_app = typer.Typer(name="approvals", help="Review and manage approval items")
meetings_app = typer.Typer(name="meetings", help="Propose and prepare meetings")
conferences_app = typer.Typer(name="conferences", help="Manage conference reminders")
feedback_app = typer.Typer(name="feedback", help="Record explicit human feedback")
learn_app = typer.Typer(name="learn", help="Manage reversible learning proposals")
app.add_typer(approvals_app, name="approvals")
app.add_typer(data_app, name="data")
app.add_typer(meetings_app, name="meetings")
app.add_typer(conferences_app, name="conferences")
app.add_typer(feedback_app, name="feedback")
app.add_typer(learn_app, name="learn")


def _print_llm_status(llm: LLM | None = None) -> None:
    """Make offline mode or the configured live model visible before work starts."""

    if llm is not None:
        if isinstance(llm, FakeLLM):
            typer.echo("LLM: FAKE (no credential)")
        else:
            typer.echo(f"LLM: LIVE ({llm.get_model_name()})")
        return

    credential = (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ANTHROPIC_API_KEY")
    )
    if credential and os.getenv("DSBOT_MODE", "").casefold() != "mock":
        typer.echo(f"LLM: LIVE ({load_settings().agent_model})")
    else:
        typer.echo("LLM: FAKE (no credential)")


@app.command("run-once")
def run_once_command(
    reprocess: bool = typer.Option(False, "--reprocess", help="Reprocess already classified mail"),
) -> None:
    """Run one complete mail, data, meeting, and reminders cycle."""

    llm = get_llm()
    _print_llm_status(llm)
    typer.echo(json.dumps(run_cycle(llm=llm, reprocess=reprocess), indent=2, default=str))


@app.command("start")
def start_command() -> None:
    """Start the recurring five-minute and scheduled daily/weekly jobs."""

    typer.echo("Starting dsbot scheduler (local time). Press Ctrl+C to stop.")
    start_scheduler()


@app.command("digest")
def digest_command(
    output_dir: Path = typer.Option("outputs/digest", "--output-dir"),
) -> None:
    """Write today's operational digest."""

    _print_llm_status()
    typer.echo(str(write_digest(output_dir=output_dir)))


def setup_testing_environment():
    """Set up testing environment with mocks."""
    os.environ["AGENT_MODEL"] = "fake-model"
    llm = get_llm(testing=True)
    audit_log = get_audit_log("outputs/audit.log")
    approval_queue = get_approval_queue(testing=True)
    return llm, audit_log, approval_queue


@app.command()
def email_triage(
    email_file: Path = typer.Argument(..., help="Path to JSON file with email data"),
    output_dir: Path = typer.Option("outputs/email", help="Output directory for drafts"),
):
    """Triage emails and draft replies."""
    llm, audit_log, approval_queue = setup_testing_environment()

    import json

    email_data = json.loads(email_file.read_text())
    email = Email(**email_data)

    triage = EmailTriage(llm)
    result = triage.classify(email)

    typer.echo(f"Category: {result.category.value}")
    typer.echo(f"Priority: {result.priority.value}")
    typer.echo(f"Summary: {result.summary}")
    typer.echo(f"Action: {result.suggested_action}")
    typer.echo(f"Confidence: {result.confidence:.2f}")
    typer.echo(f"Requires human: {result.requires_human}")

    if result.requires_human or result.priority.value in ("high", "urgent"):
        drafter = EmailDrafter(llm)
        draft = drafter.draft_reply(email, result)
        approval = queue_draft_for_approval(draft, result, email, approval_queue, audit_log)
        typer.echo(f"Draft queued for approval: {approval.id}")

    audit_log.log_action(
        "email_triage",
        actor="bot",
        details={"email_id": email.id, "category": result.category.value},
    )


@app.command("triage")
def triage_mailbox() -> None:
    """Triage the mock mailbox and print results sorted by priority."""

    provider = MockMailProvider()
    llm = get_llm()
    _print_llm_status(llm)
    triage = EmailTriage(llm)
    results = triage.triage_mailbox(provider)

    typer.echo(
        "SCORE | PRIORITY | CATEGORY         | REVIEW | SENDER                       | SUBJECT"
    )
    typer.echo(
        "------|----------|------------------|--------|------------------------------|--------"
    )
    for result in results:
        review = "yes" if result.needs_human_review else "no"
        typer.echo(
            f"{result.priority_score:>5} | "
            f"{result.priority.value:<8} | "
            f"{result.category.value:<16} | "
            f"{review:<6} | "
            f"{result.sender:<28} | "
            f"{result.subject}"
        )


@approvals_app.command("list")
def list_approvals_command() -> None:
    """List pending approval items."""

    approvals = list_pending_approvals()
    if not approvals:
        typer.echo("No pending approvals")
        return
    typer.echo("ID | KIND | CREATED")
    typer.echo("---|------|--------")
    for approval in approvals:
        typer.echo(f"{approval['id']} | {approval['kind']} | {approval['created_at']}")


@approvals_app.command("show")
def show_approval_command(approval_id: str) -> None:
    """Show one approval item as JSON."""

    approval = get_approval(approval_id)
    if approval is None:
        raise typer.BadParameter(f"Approval {approval_id} not found")
    typer.echo(json.dumps(approval, indent=2, default=str))


@approvals_app.command("approve")
def approve_approval_command(
    approval_id: str,
    variant: int = typer.Option(1, "--variant", min=1, help="1-based reply variant number"),
    slot: int | None = typer.Option(None, "--slot", min=1, help="1-based meeting slot number"),
    final_text: str | None = typer.Option(
        None,
        "--final-text",
        help="Human-edited final body to store as feedback and draft",
    ),
) -> None:
    """Approve an email reply or meeting slot after human review."""

    try:
        approval = get_approval(approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval["kind"] == "meeting_reply":
            if slot is None:
                raise ValueError("Meeting approvals require --slot")
            slots = approval["payload"].get("slots", [])
            if slot > len(slots):
                raise ValueError("Selected meeting slot is not available")
            approve_sqlite(approval_id)
            record_meeting_slot_choice(approval_id, slots[slot - 1], accepted=True)
            event = approve_meeting_reply(approval_id, MockCalendarProvider(), slot - 1)
            typer.echo(f"Meeting slot approved: {slot}")
            typer.echo(f"Calendar event created after approval: {event.id}")
            return
        draft_path = approve_email_reply(approval_id, variant, final_text=final_text)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Draft created: {draft_path}")


@approvals_app.command("reject")
def reject_approval_command(
    approval_id: str,
    final_text: str | None = typer.Option(
        None,
        "--final-text",
        help="Human-edited final text to retain with the rejection",
    ),
    slot: int | None = typer.Option(None, "--slot", min=1),
) -> None:
    """Reject one pending approval item."""

    try:
        approval = get_approval(approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval["kind"] == "meeting_reply" and slot is not None:
            slots = approval["payload"].get("slots", [])
            if slot > len(slots):
                raise ValueError("Selected meeting slot is not available")
            rejected = reject_approval(approval_id, final_text=final_text)
            record_meeting_slot_choice(approval_id, slots[slot - 1], accepted=False)
        else:
            rejected = reject_approval(approval_id, final_text=final_text)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Rejected: {rejected['id']}")


def _run_data_file(source_path: Path, output_excel: Path | None = None) -> Path:
    """Run the configured pipeline for a supported data file and create a workbook."""

    result = run_data_pipeline(source_path)
    destination = output_excel or Path("outputs/data") / f"{source_path.stem}.xlsx"
    output_path = build_workbook(result, destination)
    typer.echo(str(output_path))
    return output_path


@data_app.command("run")
def data_run_command(
    source_path: Path = typer.Argument(..., help="Path to CSV, XLSX, or JSON data"),
    output_excel: Path | None = typer.Option(
        None, "--output", "-o", help="Output Excel workbook path"
    ),
) -> None:
    """Run the data pipeline and build an Excel workbook."""

    _run_data_file(source_path, output_excel)


@app.command("process-data", deprecated=True)
def process_data(
    source_path: Path = typer.Argument(..., help="Path to CSV, XLSX, or JSON data"),
    output_excel: Path | None = typer.Option(None, "--output", "-o", help="Output workbook"),
) -> None:
    """Deprecated alias for `dsbot data run`."""

    _run_data_file(source_path, output_excel)


@app.command("report")
def report_command(
    source_path: Path = typer.Argument(..., help="Path to CSV, Excel, JSON, or PDF data"),
):
    """Run the data pipeline and create the Excel workbook and DOCX report."""

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    stem = source_path.stem
    excel_path = Path("outputs/excel") / f"{stem}_{timestamp}.xlsx"
    report_path = Path("outputs/reports") / f"{stem}_{timestamp}.docx"

    result = run_data_pipeline(source_path)
    facts = analyze(result.clean_df)
    build_workbook(result, excel_path)
    build_report(result, facts, report_path, llm=get_llm())

    typer.echo(f"Excel saved to {excel_path}")
    typer.echo(f"Report saved to {report_path}")


@meetings_app.command("propose")
def meetings_propose(
    email_file: Path = typer.Argument(..., help="Path to a meeting-request email JSON file"),
):
    """Extract a meeting request and queue three slots for human approval."""

    email_data = json.loads(email_file.read_text(encoding="utf-8"))
    email = Email.model_validate(email_data)
    calendar = MockCalendarProvider()
    scheduler = EmailMeetingScheduler(calendar, get_llm())
    proposal = scheduler.propose_from_email(email, classification=email_data.get("category"))
    typer.echo(f"Meeting reply approval: {proposal.approval_id}")
    for index, slot in enumerate(proposal.slots, start=1):
        typer.echo(f"{index}. {slot.start.isoformat()} - {slot.end.isoformat()} ({slot.timezone})")
    if proposal.reschedule_approval_ids:
        typer.echo(f"Reschedule approvals: {', '.join(proposal.reschedule_approval_ids)}")


@meetings_app.command("brief")
def meetings_brief(
    event_id: str = typer.Argument(..., help="Calendar event ID"),
):
    """Generate a prep brief for a calendar event."""

    calendar = MockCalendarProvider()
    now = datetime.now(UTC)
    events = calendar.get_events(now - timedelta(days=365), now + timedelta(days=365))
    event = next((item for item in events if item.id == event_id), None)
    if event is None:
        raise typer.BadParameter(f"Event {event_id} not found")
    output_path = generate_prep_brief(event, llm=get_llm())
    typer.echo(f"Brief saved to {output_path}")


@meetings_app.command("minutes")
def meetings_minutes(
    transcript: Path = typer.Argument(..., help="Path to a transcript text file"),
):
    """Generate validated minutes and action items from a transcript."""

    output_path = generate_minutes(transcript, get_llm())
    typer.echo(f"Minutes saved to {output_path}")


@conferences_app.command("remind")
def conferences_remind(
    conference_file: Path = typer.Option("data/conferences.yaml", "--file"),
):
    """Generate 14-, 7-, and 1-day conference deadline reminders."""

    reminders = generate_reminders(conference_file)
    typer.echo(json.dumps([item.model_dump(mode="json") for item in reminders], indent=2))


@feedback_app.command("mark")
def feedback_mark(
    email_id: str = typer.Argument(..., help="Email ID being corrected"),
    important: bool = typer.Option(False, "--important"),
    not_important: bool = typer.Option(False, "--not-important"),
    category: str | None = typer.Option(None, "--category"),
):
    """Store one explicit human importance or category correction."""

    selected = int(important) + int(not_important) + int(category is not None)
    if selected != 1:
        raise typer.BadParameter(
            "Choose exactly one of --important, --not-important, or --category"
        )
    label = "important" if important else "not-important" if not_important else "category"
    feedback_id = mark_email(email_id, label=label, category=category)
    typer.echo(f"Feedback recorded: {feedback_id}")


@learn_app.command("propose")
def learn_propose(
    kind: str = typer.Option("style", "--kind", help="style, triage, or meeting"),
):
    """Create a golden-set-gated learning proposal."""

    if kind == "style":
        proposal = learn_style_proposal(get_llm())
    elif kind == "triage":
        proposal = propose_priority_tuning()
    elif kind == "meeting":
        proposal = propose_meeting_preferences()
    else:
        raise typer.BadParameter("--kind must be style, triage, or meeting")
    typer.echo(proposal.model_dump_json())


@learn_app.command("list")
def learn_list() -> None:
    """List learning proposals and their statuses."""

    for proposal in list_proposals():
        typer.echo(
            f"{proposal.id} | {proposal.kind} | {proposal.status} | version={proposal.version}"
        )


@learn_app.command("approve")
def learn_approve(proposal_id: str = typer.Argument(...)) -> None:
    """Apply a pending learning proposal after human approval."""

    proposal = approve_proposal(proposal_id)
    typer.echo(proposal.model_dump_json())


@learn_app.command("rollback")
def learn_rollback(version: str = typer.Argument(...)) -> None:
    """Restore the configuration snapshot captured before a learning change."""

    rollback_proposal(version)
    typer.echo(f"Rolled back learning version: {version}")


@app.command()
def schedule_meeting(
    title: str = typer.Argument(..., help="Meeting title"),
    organizer: str = typer.Argument(..., help="Organizer email"),
    attendees: list[str] = typer.Option([], help="Attendee emails"),
    duration: int = typer.Option(60, help="Duration in minutes"),
    days_ahead: int = typer.Option(1, help="Days ahead to schedule"),
):
    """Schedule a meeting."""
    llm, audit_log, approval_queue = setup_testing_environment()

    scheduler = MeetingScheduler(MockCalendarConnector())
    start = datetime.utcnow() + timedelta(days=days_ahead)
    start = start.replace(hour=10, minute=0, second=0, microsecond=0)

    meeting = scheduler.propose_meeting(
        title=title,
        organizer=organizer,
        attendees=[EmailStr(a) for a in attendees],
        duration_minutes=duration,
        preferred_times=[TimeSlot(start=start, end=start + timedelta(minutes=duration))],
    )

    typer.echo(f"Proposed meeting: {meeting.id}")
    typer.echo(f"Title: {meeting.title}")
    typer.echo(f"Time: {meeting.start_time} - {meeting.end_time}")

    # Queue for approval
    from dsbot.approval import ApprovalRequest, ApprovalType

    request = ApprovalRequest(
        type=ApprovalType.MEETING_BOOK,
        payload=meeting.model_dump(),
        metadata={"title": meeting.title, "organizer": meeting.organizer},
    )
    approval_queue.add(request)
    audit_log.log_action(
        "meeting_schedule",
        actor="bot",
        details={"meeting_id": meeting.id, "title": meeting.title},
        correlation_id=request.id,
    )
    typer.echo(f"Meeting queued for approval: {request.id}")


@app.command()
def create_conference(
    name: str = typer.Argument(..., help="Conference name"),
    organizer: str = typer.Argument(..., help="Organizer email"),
    start_date: str = typer.Argument(..., help="Start date (YYYY-MM-DD)"),
    end_date: str = typer.Argument(..., help="End date (YYYY-MM-DD)"),
    location: str = typer.Option("", help="Physical location"),
    virtual_link: str = typer.Option("", help="Virtual meeting link"),
    max_attendees: int = typer.Option(0, help="Max attendees (0 = unlimited)"),
):
    """Create a conference."""
    llm, audit_log, approval_queue = setup_testing_environment()

    manager = ConferenceManager()
    conference = manager.create_conference(
        name=name,
        organizer=organizer,
        start_date=datetime.fromisoformat(start_date),
        end_date=datetime.fromisoformat(end_date),
        location=location or None,
        virtual_link=virtual_link or None,
        max_attendees=max_attendees if max_attendees > 0 else None,
    )

    typer.echo(f"Created conference: {conference.id}")
    typer.echo(f"Name: {conference.name}")
    typer.echo(f"Dates: {conference.start_date.date()} to {conference.end_date.date()}")

    queue_conference_for_approval(conference, approval_queue, audit_log)
    typer.echo("Conference queued for approval")


@app.command()
def list_approvals():
    """List pending approval requests."""
    _, _, approval_queue = setup_testing_environment()

    pending = approval_queue.list_pending()
    if not pending:
        typer.echo("No pending approvals")
        return

    for req in pending:
        typer.echo(f"ID: {req.id} | Type: {req.type.value} | Created: {req.created_at}")
        typer.echo(f"  Payload: {req.payload}")


@app.command()
def approve(request_id: str, reason: str = ""):
    """Approve a request."""
    _, audit_log, approval_queue = setup_testing_environment()

    approval_queue.approve(request_id, "human", reason)
    audit_log.log_action(
        "approval_approved",
        actor="human",
        details={"approval_id": request_id, "reason": reason},
    )
    typer.echo(f"Approved: {request_id}")


@app.command()
def reject(request_id: str, reason: str = ""):
    """Reject a request."""
    _, audit_log, approval_queue = setup_testing_environment()

    approval_queue.reject(request_id, "human", reason)
    audit_log.log_action(
        "approval_rejected",
        actor="human",
        details={"approval_id": request_id, "reason": reason},
    )
    typer.echo(f"Rejected: {request_id}")


@app.command()
def audit_logs(since: str | None = typer.Option(None, help="Show logs since (ISO format)")):
    """Show audit logs."""
    audit_log = get_audit_log()

    since_dt = datetime.fromisoformat(since) if since else None
    logs = audit_log.read_logs(since_dt)

    for log in logs:
        action = getattr(log.action_type, "value", log.action_type)
        typer.echo(f"{log.timestamp} | {action} | {log.actor} | {log.details}")


if __name__ == "__main__":
    app()
