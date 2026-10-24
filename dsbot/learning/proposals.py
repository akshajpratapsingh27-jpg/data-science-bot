"""Human-approved, golden-set-gated learning proposals."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from dsbot.audit import log
from dsbot.learning.evaluator import golden_gate
from dsbot.learning.versions import apply_diff, rollback, snapshot


class Proposal(BaseModel):
    """A reversible learning change awaiting human approval."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: str
    diff: dict[str, Any]
    expected_effect: str
    golden_before: dict[str, Any]
    golden_after: dict[str, Any]
    status: Literal["pending", "approved", "rejected", "rolled_back"] = "pending"
    rejection_reason: str | None = None
    version: str | None = None


def _proposal_path(proposal_id: str, proposals_dir: str | Path = "data/proposals") -> Path:
    directory = Path(proposals_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{proposal_id}.json"


def _save(proposal: Proposal, proposals_dir: str | Path = "data/proposals") -> Proposal:
    _proposal_path(proposal.id, proposals_dir).write_text(
        proposal.model_dump_json(indent=2), encoding="utf-8"
    )
    return proposal


def create_proposal(
    kind: str,
    diff: dict[str, Any],
    expected_effect: str,
    golden_before: dict[str, Any],
    golden_after: dict[str, Any],
    proposals_dir: str | Path = "data/proposals",
) -> Proposal:
    """Create a pending proposal or auto-reject it when the golden gate fails."""

    allowed, reason = golden_gate(golden_before, golden_after)
    proposal = Proposal(
        kind=kind,
        diff=diff,
        expected_effect=expected_effect,
        golden_before=golden_before,
        golden_after=golden_after,
        status="pending" if allowed else "rejected",
        rejection_reason=None if allowed else reason,
    )
    _save(proposal, proposals_dir)
    log(
        "bot",
        "learning_proposal_created",
        proposal_id=proposal.id,
        kind=kind,
        status=proposal.status,
        reason=reason,
    )
    return proposal


def get_proposal(proposal_id: str, proposals_dir: str | Path = "data/proposals") -> Proposal | None:
    path = _proposal_path(proposal_id, proposals_dir)
    if not path.exists():
        return None
    return Proposal.model_validate_json(path.read_text(encoding="utf-8"))


def list_proposals(proposals_dir: str | Path = "data/proposals") -> list[Proposal]:
    directory = Path(proposals_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return [
        Proposal.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def approve_proposal(
    proposal_id: str,
    proposals_dir: str | Path = "data/proposals",
    versions_dir: str | Path = "data/versions",
    project_root: str | Path = ".",
) -> Proposal:
    """Apply a still-safe proposal only after this explicit human approval."""

    proposal = get_proposal(proposal_id, proposals_dir)
    if proposal is None:
        raise ValueError(f"Proposal {proposal_id} not found")
    if proposal.status != "pending":
        raise ValueError(f"Proposal {proposal_id} is already {proposal.status}")
    allowed, reason = golden_gate(proposal.golden_before, proposal.golden_after)
    if not allowed:
        proposal.status = "rejected"
        proposal.rejection_reason = reason
        _save(proposal, proposals_dir)
        return proposal
    version = snapshot(versions_dir, project_root)
    apply_diff(proposal.diff, project_root)
    proposal.status = "approved"
    proposal.version = version
    _save(proposal, proposals_dir)
    log("human", "learning_proposal_approved", proposal_id=proposal.id, version=version)
    return proposal


def rollback_proposal(
    version: str,
    versions_dir: str | Path = "data/versions",
    project_root: str | Path = ".",
) -> None:
    rollback(version, versions_dir, project_root)
    log("human", "learning_version_rolled_back", version=version)
