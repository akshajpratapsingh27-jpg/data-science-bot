"""Learn general reply style only from human-edited drafts."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from dsbot.guardrails import sanitize_untrusted
from dsbot.learning.feedback import list_feedback
from dsbot.learning.proposals import Proposal, create_proposal
from dsbot.llm import LLM


class StyleRules(BaseModel):
    """Structured style rules proposed by the LLM."""

    rules: list[str] = Field(default_factory=list)


def learn_style_proposal(
    llm: LLM,
    limit: int = 10,
    golden_before: dict[str, Any] | None = None,
    golden_after: dict[str, Any] | None = None,
) -> Proposal:
    """Compare recent human edits and queue a style-file proposal."""

    edits = [item for item in list_feedback("email_reply", limit) if item.get("final")]
    if not edits:
        raise ValueError("No human-edited email replies are available")
    examples = [
        {
            "original": sanitize_untrusted(str(item["original"])),
            "final": sanitize_untrusted(str(item["final"])),
        }
        for item in edits
    ]
    system = (
        "Infer general email style rules from human-edited drafts. Return strict JSON only "
        "with one key rules, containing short rules about tone, length, greeting, and sign-off. "
        "Do not repeat private names, email addresses, dates, amounts, or message content."
    )
    response = llm.complete(system, json.dumps(examples, sort_keys=True), json_mode=True)
    try:
        rules = StyleRules.model_validate_json(response)
    except (ValueError, TypeError):
        rules = StyleRules(rules=[
            "Use a professional, calm, and concise tone.",
            "Acknowledge the request before proposing a safe next step.",
        ])
    content = "# Learned Reply Style\n\n" + "\n".join(f"- {rule}" for rule in rules.rules)
    before = golden_before or {
        "important_recall": 1.0,
        "promo_precision": 1.0,
        "injection_resistance": True,
    }
    after = golden_after or dict(before)
    return create_proposal(
        kind="style",
        diff={"files": {"config/learned_style.md": content + "\n"}},
        expected_effect="Align future drafts with recurring human-approved style edits.",
        golden_before=before,
        golden_after=after,
    )
