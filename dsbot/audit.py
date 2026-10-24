"""Auditable action logging backed by the core SQLite store."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from dsbot.db import SQLiteStore, get_db, json_dumps, utc_now
from dsbot.redact import redact_sensitive


class ActionType(str, Enum):
    """Known action names used by the existing modules."""

    EMAIL_TRIAGE = "email_triage"
    EMAIL_DRAFT = "email_draft"
    EMAIL_APPROVAL_QUEUED = "email_approval_queued"
    DATA_EXTRACT = "data_extract"
    DATA_CLEAN = "data_clean"
    DATA_EXCEL_EXPORT = "data_excel_export"
    DATA_REPORT_GENERATE = "data_report_generate"
    MEETING_SCHEDULE = "meeting_schedule"
    MEETING_CONFERENCE_CREATE = "meeting_conference_create"
    APPROVAL_CREATED = "approval_created"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"


class AuditEntry(BaseModel):
    """A legacy-compatible representation of an audit entry."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    action_type: ActionType | str
    actor: str
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


def _action_value(action: ActionType | str) -> str:
    return action.value if isinstance(action, ActionType) else action


def _write_log(
    store: SQLiteStore,
    actor: str,
    action: ActionType | str,
    details: dict[str, Any],
) -> int:
    safe_details = redact_sensitive(details)
    return store.execute(
        "INSERT INTO audit_log (ts, actor, action, details_json) VALUES (?, ?, ?, ?)",
        (
            utc_now(),
            redact_sensitive(actor),
            redact_sensitive(_action_value(action)),
            json_dumps(safe_details),
        ),
    )


def log(actor: str, action: str, **details: Any) -> int:
    """Write one auditable action to the default SQLite store."""

    return _write_log(get_db(), actor, action, details)


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    try:
        decoded["details"] = json.loads(decoded["details_json"])
    except (KeyError, TypeError, json.JSONDecodeError):
        decoded["details"] = {}
    return decoded


def read_last(n: int = 10) -> list[dict[str, Any]]:
    """Read the newest ``n`` audit entries, newest first."""

    if n < 1:
        return []
    rows = get_db().fetch_all(
        "SELECT id, ts, actor, action, details_json FROM audit_log ORDER BY id DESC LIMIT ?",
        (n,),
    )
    return [_decode_row(row) for row in rows]


read_last_entries = read_last


class AuditLog:
    """Compatibility facade for the earlier audit-log interface."""

    def __init__(self, log_path: str = "outputs/audit.log", db: SQLiteStore | None = None):
        del log_path  # The core audit log is now always persisted in SQLite.
        self.db = db or get_db()

    def log(self, entry: AuditEntry) -> None:
        details = dict(entry.details)
        if entry.correlation_id is not None:
            details["correlation_id"] = entry.correlation_id
        self.db.execute(
            "INSERT INTO audit_log (ts, actor, action, details_json) VALUES (?, ?, ?, ?)",
            (
                entry.timestamp.isoformat(),
                redact_sensitive(entry.actor),
                redact_sensitive(_action_value(entry.action_type)),
                json_dumps(redact_sensitive(details)),
            ),
        )

    def log_action(
        self,
        action_type: ActionType | str,
        actor: str,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        entry_details = dict(details or {})
        self.log(
            AuditEntry(
                action_type=action_type,
                actor=actor,
                details=entry_details,
                correlation_id=correlation_id,
            )
        )

    def read_logs(self, since: datetime | None = None) -> list[AuditEntry]:
        """Read audit entries, optionally filtered by timestamp."""

        rows = self.db.fetch_all("SELECT * FROM audit_log ORDER BY id ASC")
        entries: list[AuditEntry] = []
        for row in rows:
            timestamp = datetime.fromisoformat(row["ts"])
            if since is not None:
                compare_timestamp = timestamp.replace(tzinfo=None)
                compare_since = since.replace(tzinfo=None)
                if compare_timestamp < compare_since:
                    continue
            details = json.loads(row["details_json"])
            correlation_id = details.pop("correlation_id", None)
            try:
                action: ActionType | str = ActionType(row["action"])
            except ValueError:
                action = row["action"]
            entries.append(
                AuditEntry(
                    timestamp=timestamp,
                    action_type=action,
                    actor=row["actor"],
                    details=details,
                    correlation_id=correlation_id,
                )
            )
        return entries


_audit_log: AuditLog | None = None


def get_audit_log(log_path: str | None = None) -> AuditLog:
    """Return the process-level compatibility facade."""

    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLog(log_path or "outputs/audit.log")
    return _audit_log
