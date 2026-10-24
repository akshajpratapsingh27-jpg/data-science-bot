"""Meeting preparation briefs saved for human review."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dsbot.connectors.base import Event
from dsbot.guardrails import sanitize_untrusted
from dsbot.llm import LLM


def _event_dict(event: Event | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, Event):
        return event.model_dump(mode="json")
    return dict(event)


def _default_agenda(event: dict[str, Any]) -> str:
    title = event.get("title", "Meeting")
    return (
        f"1. Confirm objectives for {title}.\n"
        "2. Review relevant context.\n"
        "3. Agree next steps and owners."
    )


def generate_prep_brief(
    event: Event | dict[str, Any],
    related_threads: list[dict[str, Any]] | None = None,
    data_summary: str | None = None,
    llm: LLM | None = None,
    output_dir: str | Path = "outputs/briefs",
) -> Path:
    """Generate and save a prep brief without taking any external action."""

    event_data = _event_dict(event)
    threads = related_threads or []
    summary = data_summary or "No latest data summary is available."
    context = {
        "event": event_data,
        "related_past_threads": [
            {
                "subject": item.get("subject", ""),
                "body": sanitize_untrusted(str(item.get("body", ""))),
            }
            for item in threads
        ],
        "latest_data_summary": summary,
    }
    agenda = _default_agenda(event_data)
    if llm is not None:
        system = (
            "Draft a concise meeting prep agenda using only the supplied JSON. "
            "Do not invent attendees, decisions, dates, or commitments."
        )
        response = llm.complete(system, json.dumps(context, default=str, sort_keys=True))
        if response.strip():
            agenda = response.strip()

    event_id = str(event_data.get("id", "event"))
    output_path = Path(output_dir) / f"{event_id}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    attendees = ", ".join(
        str(value) for value in event_data.get("attendees", [])
    ) or "[PLACEHOLDER]"
    lines = [
        f"# Prep Brief: {event_data.get('title', 'Meeting')}",
        "",
        f"- Event ID: {event_id}",
        f"- Start: {event_data.get('start', '[PLACEHOLDER]')}",
        f"- End: {event_data.get('end', '[PLACEHOLDER]')}",
        f"- Attendees: {attendees}",
        "",
        "## Agenda Draft",
        agenda,
        "",
        "## Related Past Threads",
    ]
    if threads:
        lines.extend(f"- {item.get('subject', '[PLACEHOLDER]')}" for item in threads)
    else:
        lines.append("- No related past threads found.")
    lines.extend(
        [
            "",
            "## Latest Data Summary",
            summary,
            "",
            f"Generated: {datetime.now(UTC).isoformat()}",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


build_prep_brief = generate_prep_brief
