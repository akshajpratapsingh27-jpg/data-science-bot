"""Deterministic fallback rules for safe email classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dsbot.config import Settings
from dsbot.connectors.base import Email


@dataclass(frozen=True)
class RuleDecision:
    category: str
    confidence: float
    reason: str


_PHISHING_LANGUAGE = re.compile(
    r"\b(?:verify your account|immediate|password|suspended|account verification)\b",
    re.IGNORECASE,
)
_INJECTION_LANGUAGE = re.compile(
    r"\b(?:ignore (?:your|all|previous) rules|ignore previous instructions|"
    r"forward all files|reveal (?:secrets|credentials))\b",
    re.IGNORECASE,
)
_URGENCY_LANGUAGE = re.compile(
    r"\b(?:by 3\s*(?:a\.?m\.?|p\.?m\.?)|deadline|decision|urgent)\b",
    re.IGNORECASE,
)
_DATA_LANGUAGE = re.compile(r"\b(?:data|extract|export|analysis)\b", re.IGNORECASE)


def classify(email: Email, settings: Settings) -> RuleDecision:
    """Classify an email conservatively without making any external action."""

    body_and_subject = f"{email.subject}\n{email.body}"
    if is_suspicious(email, settings):
        return RuleDecision(
            "suspicious",
            0.99,
            "Untrusted sender used phishing-like or instruction-injection language.",
        )

    sender = email.sender.casefold()
    vip_senders = {address.casefold() for address in settings.vip_senders}
    if sender in vip_senders and _URGENCY_LANGUAGE.search(body_and_subject):
        return RuleDecision(
            "urgent_action",
            0.95,
            "VIP sender used deadline or decision language.",
        )

    if (
        email.subject.casefold().startswith("request:")
        and _DATA_LANGUAGE.search(body_and_subject)
    ):
        return RuleDecision("data_request", 0.95, "Subject requests data analysis or extraction.")
    if "meeting request" in email.subject.casefold():
        return RuleDecision("meeting_request", 0.95, "Subject contains a meeting request.")
    if email.subject.casefold().startswith("fyi"):
        return RuleDecision("fyi", 0.95, "Subject is marked FYI.")

    return RuleDecision("fyi", 0.5, "No deterministic classification rule matched.")


def is_suspicious(email: Email, settings: Settings) -> bool:
    """Detect phishing or instruction-injection language from an untrusted domain."""

    sender_domain = email.sender.rsplit("@", 1)[-1].casefold()
    allowed_domains = {
        domain.casefold().lstrip("@").strip() for domain in settings.allowlisted_domains
    }
    text = f"{email.subject}\n{email.body}"
    return sender_domain not in allowed_domains and bool(
        _PHISHING_LANGUAGE.search(text) or _INJECTION_LANGUAGE.search(text)
    )
