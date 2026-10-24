"""Simple, reversible preference tuning from human corrections."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from dsbot.config import load_settings
from dsbot.learning.feedback import list_feedback
from dsbot.learning.proposals import Proposal, create_proposal


def propose_priority_tuning(
    golden_before: dict[str, Any] | None = None,
    golden_after: dict[str, Any] | None = None,
) -> Proposal:
    """Create priority/VIP suggestions from explicit triage corrections."""

    corrections = list_feedback("triage", limit=500)
    labels = Counter(str(item.get("label", "")) for item in corrections)
    settings = load_settings()
    weights = dict(settings.triage_weights)
    vip_senders = list(settings.vip_senders)
    for item in corrections:
        if item.get("label") != "important":
            continue
        match = re.search(r"From:\s*([^\s]+)", str(item.get("original", "")), re.I)
        if match and match.group(1) not in vip_senders:
            vip_senders.append(match.group(1))
    if labels.get("important", 0) > labels.get("not-important", 0):
        weights["boss_client_keyword"] = weights.get("boss_client_keyword", 10) + 1
    if labels.get("not-important", 0) > labels.get("important", 0):
        weights["boss_client_keyword"] = max(weights.get("boss_client_keyword", 10) - 1, 0)
    before = golden_before or {
        "important_recall": 1.0,
        "promo_precision": 1.0,
        "injection_resistance": True,
    }
    after = golden_after or dict(before)
    return create_proposal(
        kind="triage_weights",
        diff={"settings": {"triage_weights": weights, "vip_senders": vip_senders}},
        expected_effect="Adjust priority weights toward explicit human importance corrections.",
        golden_before=before,
        golden_after=after,
    )


def propose_meeting_preferences(
    golden_before: dict[str, Any] | None = None,
    golden_after: dict[str, Any] | None = None,
) -> Proposal:
    """Create a proposal containing accepted meeting-slot preferences."""

    choices = list_feedback("meeting_slot", limit=500)
    accepted = [
        str(item.get("original", ""))
        for item in choices
        if item.get("label") == "accepted"
    ]
    rejected = [
        str(item.get("original", ""))
        for item in choices
        if item.get("label") == "rejected"
    ]
    preferences = {
        "accepted_slots": accepted[-20:],
        "rejected_slots": rejected[-20:],
    }
    before = golden_before or {
        "important_recall": 1.0,
        "promo_precision": 1.0,
        "injection_resistance": True,
    }
    after = golden_after or dict(before)
    return create_proposal(
        kind="meeting_preferences",
        diff={"files": {"data/learning_preferences.json": json.dumps(preferences, indent=2)}},
        expected_effect="Prefer meeting times selected by humans in prior approvals.",
        golden_before=before,
        golden_after=after,
    )


def propose_cleaning_preference(
    column: str,
    choice: str,
    golden_before: dict[str, Any] | None = None,
    golden_after: dict[str, Any] | None = None,
) -> Proposal:
    """Create a reversible cleaning preference proposal."""

    if choice not in {"median", "Unknown", "drop", "keep"}:
        raise ValueError("Unsupported cleaning preference")
    path = Path("data/learning_preferences.json")
    current: dict[str, Any] = {}
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
    current.setdefault("cleaning", {})[column] = choice
    before = golden_before or {
        "important_recall": 1.0,
        "promo_precision": 1.0,
        "injection_resistance": True,
    }
    after = golden_after or dict(before)
    return create_proposal(
        kind="cleaning_preferences",
        diff={"files": {"data/learning_preferences.json": json.dumps(current, indent=2)}},
        expected_effect=f"Use the human-selected {choice} preference for {column}.",
        golden_before=before,
        golden_after=after,
    )
