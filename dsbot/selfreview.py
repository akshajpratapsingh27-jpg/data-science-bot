"""Weekly quality review from persisted human feedback and audit records."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from dsbot.audit import log
from dsbot.db import get_db
from dsbot.learning.evaluator import evaluate_predictions, load_golden
from dsbot.learning.proposals import list_proposals


def _as_local_date(value: str) -> date:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return timestamp.astimezone().date()


def _weekly_buckets(now: datetime, weeks: int = 8) -> list[date]:
    today = now.astimezone().date()
    this_monday = today - timedelta(days=today.weekday())
    return [this_monday - timedelta(weeks=index) for index in reversed(range(weeks))]


def _acceptance_trend(now: datetime) -> list[tuple[date, int, int]]:
    rows = get_db().fetch_all(
        "SELECT status, created_at FROM approvals WHERE kind = 'email_reply' "
        "AND status IN ('approved', 'rejected')"
    )
    buckets: dict[date, list[int]] = {week: [0, 0] for week in _weekly_buckets(now)}
    for row in rows:
        week = _as_local_date(row["created_at"])
        week -= timedelta(days=week.weekday())
        if week in buckets:
            buckets[week][0] += int(row["status"] == "approved")
            buckets[week][1] += 1
    trend: list[tuple[date, int, int]] = []
    for week, (approved, total) in buckets.items():
        trend.append((week, approved, total))
    return trend


def _edit_size_trend(now: datetime) -> list[tuple[date, float | None, int]]:
    rows = get_db().fetch_all(
        "SELECT original, final, created_at FROM feedback "
        "WHERE kind = 'email_reply' AND final != ''"
    )
    buckets: dict[date, list[float]] = {week: [] for week in _weekly_buckets(now)}
    for row in rows:
        week = _as_local_date(row["created_at"])
        week -= timedelta(days=week.weekday())
        if week not in buckets:
            continue
        original = str(row["original"])
        final = str(row["final"])
        similarity = SequenceMatcher(None, original, final).ratio()
        buckets[week].append((1.0 - similarity) * 100)
    return [
        (week, sum(values) / len(values) if values else None, len(values))
        for week, values in buckets.items()
    ]


def _golden_scores(golden_path: str | Path) -> dict[str, Any]:
    golden = load_golden(golden_path)
    rows = get_db().fetch_all(
        "SELECT email_id, classification_json FROM classifications ORDER BY created_at"
    )
    predictions: dict[str, Any] = {}
    for row in rows:
        predictions[row["email_id"]] = json.loads(row["classification_json"])
    return evaluate_predictions(golden, predictions)


def _recurring_errors() -> list[tuple[str, str, int]]:
    rows = get_db().fetch_all(
        "SELECT details_json FROM audit_log WHERE action = 'module_error'"
    )
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        details = json.loads(row["details_json"])
        counts[(str(details.get("module", "unknown")), str(details.get("error", "")))] += 1
    return [
        (module, error, count)
        for (module, error), count in counts.most_common()
        if count > 1
    ]


def _error_fix(module: str, error: str) -> str:
    lowered = error.casefold()
    if "attachment" in lowered or "file" in lowered:
        return "Check that the input file exists, is readable, and has a supported format."
    if "json" in lowered or "llm" in lowered:
        return "Review the model response schema and retry policy."
    if "calendar" in module or "meeting" in module:
        return "Check calendar availability, timezone settings, and meeting details."
    return "Inspect the module inputs and corresponding connector or configuration."


def write_self_review(
    output_dir: str | Path = "outputs/reviews",
    *,
    now: datetime | None = None,
    golden_path: str | Path = "data/golden/triage_golden.json",
) -> Path:
    """Write a dated weekly report covering draft quality, triage, learning, and errors."""

    current = (now or datetime.now().astimezone()).astimezone()
    acceptance = _acceptance_trend(current)
    edits = _edit_size_trend(current)
    corrections = get_db().fetch_one(
        "SELECT COUNT(*) AS count FROM feedback WHERE kind = 'triage'"
    )
    proposals = list_proposals()
    applied = [proposal for proposal in proposals if proposal.status == "approved"]
    pending = [proposal for proposal in proposals if proposal.status == "pending"]
    recurring = _recurring_errors()
    try:
        scores: dict[str, Any] | None = _golden_scores(golden_path)
        score_text = json.dumps(scores, indent=2, sort_keys=True)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        scores = None
        score_text = f"Unavailable: {error}"

    lines = [
        f"# Weekly self-review — {current.date().isoformat()}",
        "## Draft acceptance rate trend",
    ]
    for week, accepted, reviewed in acceptance:
        rate = f"{accepted / reviewed:.0%}" if reviewed else "n/a"
        lines.append(f"- Week of {week.isoformat()}: {rate} ({accepted}/{reviewed} reviewed)")
    lines.append("## Average edit size trend")
    for week, average, samples in edits:
        value = f"{average:.1f}%" if average is not None else "n/a"
        lines.append(f"- Week of {week.isoformat()}: {value} across {samples} edited drafts")
    lines.extend(
        [
            "## Triage corrections count",
            f"- {int(corrections['count']) if corrections else 0}",
            "## Golden-set scores",
            "```json",
            score_text,
            "```",
            "## Applied proposals",
            *(
                [f"- `{proposal.kind}` — {proposal.id}" for proposal in applied]
                or ["- None."]
            ),
            "## Pending proposals",
            *(
                [f"- `{proposal.kind}` — {proposal.id}" for proposal in pending]
                or ["- None."]
            ),
            "## Recurring errors and suggested fixes",
        ]
    )
    lines.extend(
        [
            f"- {module}: {error} (occurred {count} times). Suggested fix: "
            f"{_error_fix(module, error)}"
            for module, error, count in recurring
        ]
        or ["- No recurring errors found."]
    )

    destination = Path(output_dir) / f"{current.date().isoformat()}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("bot", "self_review_generated", path=str(destination), date=current.date().isoformat())
    return destination


generate_self_review = write_self_review
