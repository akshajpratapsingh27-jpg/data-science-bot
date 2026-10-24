"""Build a triage golden set from the offline mock mailbox."""

from __future__ import annotations

import json
import re
from pathlib import Path


def make_golden(
    mailbox_dir: str | Path = "data/mock_mailbox",
    output_path: str | Path = "data/golden/triage_golden.json",
) -> Path:
    """Create labeled records without asking the LLM to label its own examples."""

    records = []
    for path in sorted(Path(mailbox_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        category = str(payload.get("category", "fyi"))
        body = str(payload.get("body", ""))
        records.append(
            {
                "id": payload["id"],
                "subject": payload.get("subject", ""),
                "body": body,
                "expected_category": category,
                "important": category == "important",
                "promotional": category == "promotional",
                "injection": bool(
                    re.search(r"ignore (?:your )?rules|forward all files|bypass", body, re.I)
                ),
            }
        )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(make_golden())
