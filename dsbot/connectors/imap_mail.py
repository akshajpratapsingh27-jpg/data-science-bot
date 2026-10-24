"""IMAP mail provider stub for a future production connector."""

from __future__ import annotations

from typing import Any

from dsbot.connectors.base import Email, MailProvider


class IMAPMailProvider(MailProvider):
    """Placeholder for an IMAP-backed provider."""

    def fetch_unread(self) -> list[Email]:
        # TODO: implement authenticated IMAP search and normalization.
        raise NotImplementedError

    def mark(self, email_id: str, label: str) -> None:
        # TODO: implement IMAP label/flag updates.
        raise NotImplementedError

    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        thread_id: str | None = None,
        cc: list[str] | None = None,
    ) -> Any:
        # TODO: implement IMAP draft creation without sending.
        raise NotImplementedError
