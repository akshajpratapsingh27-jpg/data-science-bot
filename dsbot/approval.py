"""Approval queue for dsbot - human-in-the-loop gate."""

import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalType(str, Enum):
    """Types of approval requests."""

    EMAIL_SEND = "email_send"
    MEETING_BOOK = "meeting_book"
    CONFERENCE_BOOK = "conference_book"
    DATA_REPORT_RELEASE = "data_report_release"


class ApprovalRequest(BaseModel):
    """An item awaiting human approval."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: ApprovalType
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)  # The actual content to approve
    metadata: dict[str, Any] = Field(default_factory=dict)  # Context for the human reviewer
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None


class ApprovalQueue(ABC):
    """Abstract interface for approval queues."""

    @abstractmethod
    def add(self, request: ApprovalRequest) -> ApprovalRequest:
        """Add a new approval request."""
        ...

    @abstractmethod
    def get(self, request_id: str) -> ApprovalRequest | None:
        """Get an approval request by ID."""
        ...

    @abstractmethod
    def list_pending(self) -> list[ApprovalRequest]:
        """List all pending approval requests."""
        ...

    @abstractmethod
    def approve(self, request_id: str, decided_by: str, reason: str = "") -> ApprovalRequest:
        """Approve a request."""
        ...

    @abstractmethod
    def reject(self, request_id: str, decided_by: str, reason: str = "") -> ApprovalRequest:
        """Reject a request."""
        ...


class FileApprovalQueue(ApprovalQueue):
    """File-based approval queue implementation."""

    def __init__(self, queue_dir: Path | str = "outputs/approvals"):
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _get_file_path(self, request_id: str) -> Path:
        return self.queue_dir / f"{request_id}.json"

    def add(self, request: ApprovalRequest) -> ApprovalRequest:
        with self._lock:
            file_path = self._get_file_path(request.id)
            request.updated_at = datetime.utcnow()
            file_path.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        return request

    def get(self, request_id: str) -> ApprovalRequest | None:
        file_path = self._get_file_path(request_id)
        if not file_path.exists():
            return None
        return ApprovalRequest.model_validate_json(file_path.read_text(encoding="utf-8"))

    def list_pending(self) -> list[ApprovalRequest]:
        requests = []
        for file_path in self.queue_dir.glob("*.json"):
            try:
                request = ApprovalRequest.model_validate_json(file_path.read_text(encoding="utf-8"))
                if request.status == ApprovalStatus.PENDING:
                    requests.append(request)
            except Exception:
                continue
        return sorted(requests, key=lambda r: r.created_at)

    def _update_status(
        self,
        request_id: str,
        status: ApprovalStatus,
        decided_by: str,
        reason: str,
    ) -> ApprovalRequest:
        with self._lock:
            request = self.get(request_id)
            if request is None:
                raise ValueError(f"Approval request {request_id} not found")

            request.status = status
            request.decided_by = decided_by
            request.decided_at = datetime.utcnow()
            request.decision_reason = reason
            request.updated_at = datetime.utcnow()

            file_path = self._get_file_path(request_id)
            file_path.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        return request

    def approve(self, request_id: str, decided_by: str, reason: str = "") -> ApprovalRequest:
        return self._update_status(request_id, ApprovalStatus.APPROVED, decided_by, reason)

    def reject(self, request_id: str, decided_by: str, reason: str = "") -> ApprovalRequest:
        return self._update_status(request_id, ApprovalStatus.REJECTED, decided_by, reason)


class MockApprovalQueue(ApprovalQueue):
    """In-memory mock approval queue for testing."""

    def __init__(self):
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    def add(self, request: ApprovalRequest) -> ApprovalRequest:
        with self._lock:
            self._requests[request.id] = request
        return request

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._requests.get(request_id)

    def list_pending(self) -> list[ApprovalRequest]:
        return sorted(
            [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING],
            key=lambda r: r.created_at,
        )

    def approve(self, request_id: str, decided_by: str, reason: str = "") -> ApprovalRequest:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise ValueError(f"Approval request {request_id} not found")
            request.status = ApprovalStatus.APPROVED
            request.decided_by = decided_by
            request.decided_at = datetime.utcnow()
            request.decision_reason = reason
            request.updated_at = datetime.utcnow()
        return request

    def reject(self, request_id: str, decided_by: str, reason: str = "") -> ApprovalRequest:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise ValueError(f"Approval request {request_id} not found")
            request.status = ApprovalStatus.REJECTED
            request.decided_by = decided_by
            request.decided_at = datetime.utcnow()
            request.decision_reason = reason
            request.updated_at = datetime.utcnow()
        return request


# Global approval queue instance
_approval_queue: ApprovalQueue | None = None


def get_approval_queue(testing: bool = False, **kwargs) -> ApprovalQueue:
    """Get or create the global approval queue instance."""
    global _approval_queue
    if _approval_queue is None:
        if testing:
            _approval_queue = MockApprovalQueue()
        else:
            _approval_queue = FileApprovalQueue(**kwargs)
    return _approval_queue
