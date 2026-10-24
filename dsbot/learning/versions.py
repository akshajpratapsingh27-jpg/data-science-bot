"""Numbered configuration snapshots and exact rollback."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

PROJECT_FILES = (
    Path("config/settings.yaml"),
    Path("config/style.md"),
    Path("config/learned_style.md"),
    Path("data/learning_preferences.json"),
)
PROTECTED_PATHS = {
    Path("dsbot/guardrails.py"),
}
PROTECTED_FILE_DIFFS = {Path("config/settings.yaml")}
ALLOWED_FILE_DIFFS = {
    Path("config/learned_style.md"),
    Path("data/learning_preferences.json"),
}
PROTECTED_SETTINGS = {"autonomy_level", "approval_requirements", "guardrails", "blocked_actions"}


def _version_number(versions_dir: Path) -> int:
    values = [
        int(path.name)
        for path in versions_dir.iterdir()
        if path.is_dir() and path.name.isdigit()
    ]
    return max(values, default=0) + 1


def snapshot(
    versions_dir: str | Path = "data/versions",
    project_root: str | Path = ".",
    paths: tuple[Path, ...] = PROJECT_FILES,
) -> str:
    """Create a numbered snapshot before applying a proposal."""

    root = Path(project_root)
    directory = Path(versions_dir)
    directory.mkdir(parents=True, exist_ok=True)
    version = f"{_version_number(directory):04d}"
    target = directory / version
    target.mkdir()
    present: list[str] = []
    tracked = [path.as_posix() for path in paths]
    for relative in paths:
        source = root / relative
        if not source.exists():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        present.append(relative.as_posix())
    (target / "manifest.json").write_text(
        json.dumps({"tracked": tracked, "present": present}, indent=2),
        encoding="utf-8",
    )
    return version


def _safe_relative(path: str | Path) -> Path:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe learning target path: {path}")
    if relative in PROTECTED_PATHS or relative in PROTECTED_FILE_DIFFS:
        raise ValueError(f"Learning cannot modify protected path: {relative}")
    if relative not in ALLOWED_FILE_DIFFS:
        raise ValueError(f"Learning target is outside the allowed learning files: {relative}")
    return relative


def apply_diff(
    diff: dict[str, Any],
    project_root: str | Path = ".",
) -> None:
    """Apply only allowed learning targets."""

    root = Path(project_root)
    files = diff.get("files", {})
    if not isinstance(files, dict):
        raise ValueError("Proposal file diff must be a mapping")
    for raw_path, content in files.items():
        relative = _safe_relative(raw_path)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(str(content), encoding="utf-8")

    settings_diff = diff.get("settings", {})
    if settings_diff:
        if not isinstance(settings_diff, dict):
            raise ValueError("Proposal settings diff must be a mapping")
        if any(str(key).casefold() in PROTECTED_SETTINGS for key in settings_diff):
            raise ValueError("Learning cannot modify guardrails, autonomy, or approvals")
        settings_path = root / "config/settings.yaml"
        values = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        for key, value in settings_diff.items():
            if str(key).casefold() in PROTECTED_SETTINGS:
                raise ValueError(f"Learning cannot modify protected setting: {key}")
            values[key] = value
        settings_path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")

    deletes = diff.get("delete", [])
    if not isinstance(deletes, list):
        raise ValueError("Proposal delete diff must be a list")
    for raw_path in deletes:
        relative = _safe_relative(raw_path)
        destination = root / relative
        if destination.exists():
            destination.unlink()


def rollback(
    version: str,
    versions_dir: str | Path = "data/versions",
    project_root: str | Path = ".",
) -> None:
    """Restore all tracked files to the exact state captured by a version."""

    root = Path(project_root)
    target = Path(versions_dir) / str(version)
    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Version {version} not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    present = set(manifest.get("present", []))
    for raw_path in manifest.get("tracked", []):
        relative = Path(raw_path)
        destination = root / relative
        source = target / relative
        if raw_path in present:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.exists():
            destination.unlink()
