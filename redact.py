"""Local-only reversible redaction for sensitive text sent to language models."""

from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?)\d{3,4}[ .-]\d{3,4}(?!\w)")
_LONG_NUMBER = re.compile(r"(?<!\w)\d{8,}(?!\w)")


@dataclass(frozen=True)
class RedactedText:
    """Text with a per-call replacement map kept only in process memory."""

    text: str
    replacements: dict[str, str]

    def restore(self, value: str) -> str:
        """Restore tokens in a model response without persisting the mapping."""

        for token, original in self.replacements.items():
            value = value.replace(token, original)
        return value


def redact_text(value: str, token_prefix: str = "") -> RedactedText:
    """Replace email addresses, phone numbers, and long numeric identifiers."""

    replacements: dict[str, str] = {}

    def replace(pattern: re.Pattern[str], label: str, text: str) -> str:
        def substitute(match: re.Match[str]) -> str:
            token = f"[{token_prefix}{label}_{len(replacements) + 1:04d}]"
            replacements[token] = match.group(0)
            return token

        return pattern.sub(substitute, text)

    text = replace(_EMAIL, "EMAIL", value)
    text = replace(_PHONE, "PHONE", text)
    text = replace(_LONG_NUMBER, "ID", text)
    return RedactedText(text, replacements)


def redact_sensitive(value: object) -> object:
    """Redact environment-provided secrets from audit-safe values recursively."""

    import os

    secret_values = {
        item
        for name, item in os.environ.items()
        if item
        and any(
            marker in name.upper()
            for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
        )
    }
    if isinstance(value, str):
        for secret in sorted(secret_values, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED_SECRET]")
        return value
    if isinstance(value, dict):
        return {key: redact_sensitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value
