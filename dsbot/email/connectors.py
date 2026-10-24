"""Email connectors for dsbot."""

from abc import ABC, abstractmethod

from dsbot.email.triage import Email


class EmailConnector(ABC):
    """Abstract interface for email connectors."""

    @abstractmethod
    def fetch_unread(self, limit: int = 50) -> list[Email]:
        """Fetch unread emails."""
        ...

    @abstractmethod
    def mark_read(self, email_id: str) -> None:
        """Mark an email as read."""
        ...


class MockEmailConnector(EmailConnector):
    """Mock email connector for testing."""

    def __init__(self, emails: list[Email] | None = None):
        self._emails = emails or []
        self._read_ids: set[str] = set()

    def fetch_unread(self, limit: int = 50) -> list[Email]:
        unread = [e for e in self._emails if e.id not in self._read_ids]
        return unread[:limit]

    def mark_read(self, email_id: str) -> None:
        self._read_ids.add(email_id)

class GmailConnector(EmailConnector):
    """Gmail API connector (placeholder for real implementation)."""

    def __init__(self, credentials_path: str, token_path: str):
        self.credentials_path = credentials_path
        self.token_path = token_path
        # Real implementation would use googleapiclient.discovery

    def fetch_unread(self, limit: int = 50) -> list[Email]:
        # Placeholder - real implementation would call Gmail API
        raise NotImplementedError("GmailConnector not implemented")

    def mark_read(self, email_id: str) -> None:
        raise NotImplementedError("GmailConnector not implemented")

class OutlookConnector(EmailConnector):
    """Microsoft Graph / Outlook connector (placeholder)."""

    def __init__(self, client_id: str, client_secret: str, tenant_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id

    def fetch_unread(self, limit: int = 50) -> list[Email]:
        raise NotImplementedError("OutlookConnector not implemented")

    def mark_read(self, email_id: str) -> None:
        raise NotImplementedError("OutlookConnector not implemented")
