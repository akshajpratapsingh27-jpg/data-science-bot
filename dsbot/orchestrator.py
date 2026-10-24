"""Coordinate one auditable, human-approved dsbot work cycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from dsbot.approvals import create_approval
from dsbot.audit import log
from dsbot.connectors.base import CalendarProvider, Email, MailProvider
from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.connectors.mock_mail import MockMailProvider
from dsbot.data.excel import build_workbook
from dsbot.data.extract import pull_attachments
from dsbot.data.pipeline import run as run_data_pipeline
from dsbot.data.report import build_markdown_report
from dsbot.db import get_db
from dsbot.email.drafter import ELIGIBLE_CATEGORIES, EmailDrafter
from dsbot.email.triage import EmailTriage, TriageResult
from dsbot.llm import LLM, LLMBudgetExceeded, get_llm, llm_budget_paused
from dsbot.meetings.conferences import generate_reminders
from dsbot.meetings.scheduler import MeetingScheduler


def _record_error(module: str, error: Exception, email_id: str | None = None) -> dict[str, str]:
    details = {"module": module, "error_type": type(error).__name__, "error": str(error)}
    if email_id is not None:
        details["email_id"] = email_id
    log("bot", "module_error", **details)
    return details


def _run_module(
    module: str,
    action: Callable[[], Any],
    results: dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    try:
        results[module] = action()
        log("bot", "module_completed", module=module)
    except Exception as error:
        if isinstance(error, LLMBudgetExceeded):
            raise
        errors.append(_record_error(module, error))
        results[module] = None


def _triage_emails(emails: list[Email], llm: LLM) -> list[TriageResult]:
    triage = EmailTriage(llm)
    return [triage.classify(email) for email in emails]


def _draft_replies(
    emails: list[Email],
    classifications: list[TriageResult],
    drafter: EmailDrafter,
) -> list[str]:
    by_id = {email.id: email for email in emails}
    approval_ids: list[str] = []
    for classification in classifications:
        category = classification.category
        if category not in ELIGIBLE_CATEGORIES:
            continue
        email = by_id[classification.email_id]
        approval_ids.append(
            drafter.draft_and_queue(
                email,
                classification,
                current_batch_ids=set(by_id),
            )
        )
    return approval_ids


def _run_data_requests(
    emails: list[Email],
    classifications: list[TriageResult],
    llm: LLM,
    output_root: Path,
) -> list[dict[str, str]]:
    by_id = {email.id: email for email in emails}
    outputs: list[dict[str, str]] = []
    for classification in classifications:
        if classification.category.value != "data_request":
            continue
        email = by_id[classification.email_id]
        attachments = pull_attachments(email)
        if not attachments:
            raise ValueError(f"Data request {email.id} has no readable data attachments")
        for attachment in attachments:
            pipeline_result = run_data_pipeline(attachment)
            report_content = build_markdown_report(
                pipeline_result,
                llm,
                charts_dir=output_root / "charts",
            )
            report_path = output_root / "reports" / f"{email.id}_{attachment.stem}.md"
            excel_path = build_workbook(
                pipeline_result,
                output_root / "excel" / f"{email.id}_{attachment.stem}.xlsx",
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_content, encoding="utf-8")
            approval_id = create_approval(
                "data_report",
                {
                    "email_id": email.id,
                    "source": str(attachment),
                    "report_path": str(report_path),
                    "excel_path": str(excel_path),
                    "title": "Data Analysis Report",
                    "content": report_content,
                },
            )
            outputs.append(
                {
                    "email_id": email.id,
                    "approval_id": approval_id,
                    "report_path": str(report_path),
                    "excel_path": str(excel_path),
                }
            )
    return outputs


def _propose_meetings(
    emails: list[Email],
    classifications: list[TriageResult],
    scheduler: MeetingScheduler,
) -> list[str]:
    by_id = {email.id: email for email in emails}
    approval_ids: list[str] = []
    for classification in classifications:
        if classification.category.value != "meeting_request":
            continue
        proposal = scheduler.propose_from_email(by_id[classification.email_id], classification)
        approval_ids.append(proposal.approval_id)
    return approval_ids


def _generate_conference_reminders(conference_file: str | Path) -> list[dict[str, Any]]:
    reminders = generate_reminders(conference_file)
    log("bot", "conference_reminders_generated", count=len(reminders))
    return [reminder.model_dump(mode="json") for reminder in reminders]


def run_cycle(
    mail_provider: MailProvider | None = None,
    calendar_provider: CalendarProvider | None = None,
    llm: LLM | None = None,
    *,
    output_root: str | Path = "outputs",
    conference_file: str | Path = "data/conferences.yaml",
    kill_switch_path: str | Path = "data/KILL",
    reprocess: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and process mail, data requests, meetings, reminders, then write a digest."""

    kill_switch = Path(kill_switch_path)
    if kill_switch.is_file():
        log("bot", "kill_switch_activated", path=str(kill_switch))
        return {"stopped": True, "reason": "kill_switch"}
    if llm_budget_paused():
        return {"stopped": True, "reason": "llm_daily_budget"}

    active_mail = mail_provider or MockMailProvider()
    active_calendar = calendar_provider or MockCalendarProvider()
    active_llm = llm or get_llm()
    root = Path(output_root)
    results: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    fetched: list[Email] = []

    def fetch_mail() -> list[Email]:
        nonlocal fetched
        unread = active_mail.fetch_unread()
        if reprocess:
            fetched = unread
        else:
            processed = {
                row["email_id"]
                for row in get_db().fetch_all("SELECT email_id FROM classifications")
            }
            fetched = [email for email in unread if email.id not in processed]
            skipped = len(unread) - len(fetched)
            if skipped:
                log("bot", "already_processed_emails_skipped", count=skipped)
        return fetched

    classifications: list[TriageResult] = []

    def triage() -> list[TriageResult]:
        nonlocal classifications
        classifications = _triage_emails(fetched, active_llm)
        return classifications

    try:
        _run_module("fetch_mail", fetch_mail, results, errors)
        _run_module("triage", triage, results, errors)
        drafter = EmailDrafter(active_llm)
        _run_module(
            "draft_replies",
            lambda: _draft_replies(fetched, classifications, drafter),
            results,
            errors,
        )
        _run_module(
            "data_pipeline",
            lambda: _run_data_requests(fetched, classifications, active_llm, root),
            results,
            errors,
        )
        meeting_scheduler = MeetingScheduler(active_calendar, active_llm)
        _run_module(
            "meeting_proposals",
            lambda: _propose_meetings(fetched, classifications, meeting_scheduler),
            results,
            errors,
        )
        _run_module(
            "conference_reminders",
            lambda: _generate_conference_reminders(conference_file),
            results,
            errors,
        )
    except LLMBudgetExceeded:
        results.update({"stopped": True, "reason": "llm_daily_budget", "errors": errors})
        return results

    from dsbot.digest import write_digest

    try:
        results["digest"] = str(
            write_digest(
                output_dir=root / "digest",
                calendar_provider=active_calendar,
                now=now,
            )
        )
    except Exception as error:
        errors.append(_record_error("digest", error))
        results["digest"] = None
    results["errors"] = errors
    return results
