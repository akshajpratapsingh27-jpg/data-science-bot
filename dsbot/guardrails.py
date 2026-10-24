"""Guardrails for handling untrusted email content."""

from __future__ import annotations

import html
import re

BLOCKED_ACTIONS = frozenset(
    {
        "send_email",
        "send_email_directly",
        "book_meeting",
        "book_meeting_directly",
        "approve_request",
        "delete_data",
        "change_permissions",
    }
)


_INJECTION_PATTERNS = (
    r"\bignore\s+(?:all\s+)?previous\s+instructions\b",
    r"\bignore\s+(?:all\s+)?(?:your|the)\s+(?:rules|instructions)\b",
    r"\bdisregard\s+(?:all\s+)?previous\s+instructions\b",
    r"\bforget\s+(?:all\s+)?previous\s+instructions\b",
    r"\b(?:reveal|show|print)\s+(?:the\s+)?(?:system|developer)\s+prompt\b",
    r"\b(?:system|developer)\s+message\b",
    r"\byou\s+are\s+now\b",
    r"\bbypass\s+(?:your\s+)?safety\b",
    r"\bforward\s+all\s+files\s+to\b",
)


def sanitize_untrusted(text: str) -> str:
    """Mark email text as untrusted and replace common prompt injections."""

    sanitized = text
    for pattern in _INJECTION_PATTERNS:
        sanitized = re.sub(
            pattern,
            "[BLOCKED_INJECTION]",
            sanitized,
            flags=re.IGNORECASE,
        )

    sanitized = sanitized.replace("<untrusted_email>", "[BLOCKED_TAG]")
    sanitized = sanitized.replace("</untrusted_email>", "[BLOCKED_TAG]")
    return f"<untrusted_email>{html.escape(sanitized)}</untrusted_email>"
