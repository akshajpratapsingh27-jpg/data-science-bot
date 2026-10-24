"""SQLite-backed human approval queue."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from dsbot.audit import log
from dsbot.db import get_db, json_dumps, utc_now
from dsbot.learning.feedback import log_feedback


def _approval_id(value: str | Mapping[str, Any] | Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return str(value["id"])
    if hasattr(value, "id"):
        return str(value.id)
    return str(value)


def _decode_approval(row: dict[str, Any]) -> dict[str, Any]:
    approval = dict(row)
    approval["payload"] = json.loads(approval.pop("payload_json"))
    return approval


def create_approval(kind: str, payload: dict[str, Any]) -> str:
    """Create a pending approval and return its ID."""

    approval_id = str(uuid.uuid4())
    get_db().execute(
        "INSERT INTO approvals (id, kind, payload_json, status, created_at) "
        "VALUES (?, ?, ?, 'pending', ?)",
        (approval_id, kind, json_dumps(payload), utc_now()),
    )
    log("bot", "approval_created", approval_id=approval_id, kind=kind)
    return approval_id


def list_pending() -> list[dict[str, Any]]:
    """Return pending approvals in creation order."""

    rows = get_db().fetch_all(
        "SELECT id, kind, payload_json, status, created_at FROM approvals "
        "WHERE status = 'pending' ORDER BY created_at ASC"
    )
    return [_decode_approval(row) for row in rows]


def get_approval(approval_id: str | Mapping[str, Any] | Any) -> dict[str, Any] | None:
    """Return an approval by ID, including decoded payload."""

    row = get_db().fetch_one(
        "SELECT id, kind, payload_json, status, created_at FROM approvals WHERE id = ?",
        (_approval_id(approval_id),),
    )
    return _decode_approval(row) if row is not None else None


def _set_status(
    approval_id: str | Mapping[str, Any] | Any,
    status: str,
    reason: str | None = None,
    final_text: str | None = None,
) -> dict[str, Any]:
    resolved_id = _approval_id(approval_id)
    approval = get_approval(resolved_id)
    if approval is None:
        raise ValueError(f"Approval {resolved_id} not found")
    if approval["status"] != "pending":
        raise ValueError(f"Approval {resolved_id} is already {approval['status']}")

    store = get_db()
    store.execute(
        "UPDATE approvals SET status = ? WHERE id = ?",
        (status, resolved_id),
    )
    details: dict[str, Any] = {
        "approval_id": resolved_id,
        "kind": approval["kind"],
    }
    if reason:
        details["reason"] = reason
    log("human", f"approval_{status}", **details)
    _record_learning_feedback(approval, status, final_text, reason or "")
    updated = get_approval(resolved_id)
    if updated is None:  # pragma: no cover - protected by the successful update above
        raise RuntimeError(f"Approval {resolved_id} disappeared after update")
    return updated


def approve(
    approval_id: str | Mapping[str, Any] | Any,
    final_text: str | None = None,
) -> dict[str, Any]:
    """Approve a pending item and write an audit entry."""

    return _set_status(approval_id, "approved", final_text=final_text)


def reject(
    approval_id: str | Mapping[str, Any] | Any,
    reason: str = "",
    final_text: str | None = None,
) -> dict[str, Any]:
    """Reject a pending item and write an audit entry with an optional reason."""

    return _set_status(approval_id, "rejected", reason, final_text=final_text)


def _record_learning_feedback(
    approval: dict[str, Any],
    status: str,
    final_text: str | None,
    reason: str,
) -> None:
    """Persist only explicit human approval/rejection decisions as feedback."""

    if approval.get("kind") != "email_reply":
        return
    payload = approval.get("payload", {})
    variants = payload.get("variants", [])
    original = ""
    if variants and isinstance(variants[0], dict):
        original = str(variants[0].get("body", ""))
    if not original:
        original = str(payload.get("thread_history", ""))
    final = final_text if final_text is not None else (original if status == "approved" else "")
    label = "approved" if status == "approved" else f"rejected:{reason}".rstrip(":")
    email = payload.get("email", {})
    ref_id = str(email.get("id", approval.get("id", "")))
    log_feedback("email_reply", ref_id, original, final, label)
