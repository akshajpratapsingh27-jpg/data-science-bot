"""File-backed mock mail provider for offline development and tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dsbot.connectors.base import Email, MailProvider


class MockMailProvider(MailProvider):
    """Read mailbox messages from JSON files and write drafts to disk."""

    def __init__(
        self,
        mailbox_dir: Path | str = "data/mock_mailbox",
        draft_dir: Path | str = "outputs/drafts",
    ) -> None:
        self.mailbox_dir = Path(mailbox_dir)
        self.draft_dir = Path(draft_dir)
        self.mailbox_dir.mkdir(parents=True, exist_ok=True)
        self.draft_dir.mkdir(parents=True, exist_ok=True)

    def _records(self) -> list[tuple[Path, dict[str, Any]]]:
        records: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(self.mailbox_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as mailbox_file:
                record = json.load(mailbox_file)
            if isinstance(record, dict):
                records.append((path, record))
        return records

    def fetch_unread(self) -> list[Email]:
        """Return messages whose JSON record is not labeled ``read``."""

        emails: list[Email] = []
        for _, record in self._records():
            labels = {str(label).lower() for label in record.get("labels", [])}
            if "read" in labels:
                continue
            emails.append(Email.model_validate(record))
        return sorted(emails, key=lambda email: email.received_at)

    def mark(self, email_id: str, label: str) -> None:
        """Persist a label on the matching mailbox JSON record."""

        for path, record in self._records():
            if record.get("id") != email_id:
                continue
            labels = {str(item) for item in record.get("labels", [])}
            labels.add(label)
            record["labels"] = sorted(labels)
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            return
        raise ValueError(f"Email {email_id} not found in mock mailbox")

    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        thread_id: str | None = None,
        cc: list[str] | None = None,
    ) -> Path:
        """Write a draft JSON artifact without sending it."""

        draft_id = uuid.uuid4().hex
        draft_path = self.draft_dir / f"{draft_id}.json"
        draft = {
            "id": draft_id,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "body": body,
            "thread_id": thread_id,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "draft",
        }
        draft_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
        return draft_path
