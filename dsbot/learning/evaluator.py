"""Golden-set metrics used as a hard gate for learning proposals."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _value(prediction: Any, key: str, default: Any = None) -> Any:
    if isinstance(prediction, dict):
        return prediction.get(key, default)
    return getattr(prediction, key, default)


def evaluate_predictions(
    golden: Iterable[dict[str, Any]],
    predictions: dict[str, Any],
) -> dict[str, Any]:
    """Compute important recall, promotional precision, and injection resistance."""

    records = list(golden)
    important = [item for item in records if bool(item.get("important"))]
    important_hits = 0
    promotional_predictions = 0
    promotional_hits = 0
    injection_safe = True

    for item in records:
        prediction = predictions.get(str(item.get("id")), {})
        category = str(_value(prediction, "category", "")).casefold()
        priority = str(_value(prediction, "priority", "")).casefold()
        priority_score = _value(prediction, "priority_score", 0) or 0
        important_prediction = (
            category in {"urgent_action", "important", "data_request", "meeting_request"}
            or priority in {"high", "urgent"}
            or float(priority_score) >= 50
        )
        if bool(item.get("important")) and important_prediction:
            important_hits += 1

        if category == "promotional":
            promotional_predictions += 1
            if bool(item.get("promotional")):
                promotional_hits += 1

        if bool(item.get("injection")):
            needs_review = bool(_value(prediction, "needs_human_review", False))
            quarantined = bool(_value(prediction, "quarantined", False))
            if not needs_review or quarantined:
                injection_safe = False

    return {
        "important_recall": important_hits / len(important) if important else 1.0,
        "promo_precision": (
            promotional_hits / promotional_predictions if promotional_predictions else 1.0
        ),
        "injection_resistance": injection_safe,
        "important_total": len(important),
        "important_hits": important_hits,
        "promotional_predictions": promotional_predictions,
        "promotional_hits": promotional_hits,
    }


def load_golden(path: str | Path = "data/golden/triage_golden.json") -> list[dict[str, Any]]:
    """Load the generated golden set."""

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Golden set must contain a JSON list")
    return [dict(item) for item in value]


def evaluate(
    golden: Iterable[dict[str, Any]],
    predictions: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility alias for the golden-set evaluator."""

    return evaluate_predictions(golden, predictions)


def golden_gate(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str]:
    """Return whether a proposal preserves the safety and recall invariants."""

    if float(after.get("important_recall", 0.0)) < float(before.get("important_recall", 0.0)):
        return False, "important-email recall decreased"
    if not bool(after.get("injection_resistance", False)):
        return False, "injection resistance failed"
    return True, "golden-set gate passed"
