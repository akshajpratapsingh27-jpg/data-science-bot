"""Persistence for explicit human-confirmed feedback only."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from dsbot.audit import log
from dsbot.db import get_db, json_dumps, utc_now


def log_feedback(
    kind: str,
    ref_id: str,
    original: str,
    final: str,
    label: str,
) -> str:
    """Record one human-confirmed correction, approval, edit, or rejection."""

    feedback_id = str(uuid.uuid4())
    get_db().execute(
        "INSERT INTO feedback (id, kind, ref_id, original, final, label, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (feedback_id, kind, ref_id, original, final, label, utc_now()),
    )
    log(
        "human",
        "feedback_recorded",
        feedback_id=feedback_id,
        kind=kind,
        ref_id=ref_id,
        label=label,
    )
    return feedback_id


def list_feedback(kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return feedback in newest-first order."""

    if limit < 1:
        return []
    if kind is None:
        rows = get_db().fetch_all(
            "SELECT id, kind, ref_id, original, final, label, created_at "
            "FROM feedback ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    else:
        rows = get_db().fetch_all(
            "SELECT id, kind, ref_id, original, final, label, created_at "
            "FROM feedback WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        )
    return rows


def feedback_for(kind: str, ref_id: str) -> list[dict[str, Any]]:
    """Return all feedback for one human-reviewed reference."""

    return get_db().fetch_all(
        "SELECT id, kind, ref_id, original, final, label, created_at "
        "FROM feedback WHERE kind = ? AND ref_id = ? ORDER BY created_at ASC",
        (kind, ref_id),
    )


def _email_original(email_id: str) -> str:
    row = get_db().fetch_one("SELECT payload_json FROM emails WHERE id = ?", (email_id,))
    if row is not None:
        payload = json.loads(row["payload_json"])
        return "\n".join(
            [
                f"From: {payload.get('sender', '')}",
                f"Subject: {payload.get('subject', '')}",
                f"Body: {payload.get('body', '')}",
            ]
        )

    for path in sorted(Path("data/mock_mailbox").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("id") == email_id:
            return "\n".join(
                [
                    f"From: {payload.get('sender', '')}",
                    f"Subject: {payload.get('subject', '')}",
                    f"Body: {payload.get('body', '')}",
                ]
            )
    return "[PLACEHOLDER]"


def mark_email(
    email_id: str,
    label: str,
    category: str | None = None,
) -> str:
    """Store a human's explicit importance or category correction."""

    final = category or label
    feedback_label = f"category:{category}" if category else label
    return log_feedback("triage", email_id, _email_original(email_id), final, feedback_label)


def record_meeting_slot_choice(
    ref_id: str,
    slot: dict[str, Any] | str,
    accepted: bool,
) -> str:
    """Store a human's accepted or rejected meeting-slot choice."""

    slot_text = json_dumps(slot) if isinstance(slot, dict) else str(slot)
    label = "accepted" if accepted else "rejected"
    return log_feedback("meeting_slot", ref_id, slot_text, slot_text, label)
