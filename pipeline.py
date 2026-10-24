"""Extract -> profile -> clean -> validate orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from dsbot.audit import log
from dsbot.data.clean import ChangeRecord, Cleaner
from dsbot.data.extract import DataSource, get_extractor
from dsbot.data.profile import profile_df
from dsbot.data.validate import DataSchema, ValidationError, validate_df


@dataclass
class PipelineResult:
    """All stage outputs from one data pipeline run."""

    raw_df: pd.DataFrame
    clean_df: pd.DataFrame
    profile_before: dict[str, Any]
    profile_after: dict[str, Any]
    change_log: list[ChangeRecord]
    validation_report: dict[str, Any]


def _source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    types = {
        ".csv": "csv",
        ".xls": "excel",
        ".xlsx": "excel",
        ".json": "json",
        ".pdf": "pdf",
    }
    if suffix not in types:
        raise ValueError(f"Unsupported data file type: {suffix or '<none>'}")
    return types[suffix]


def run(
    path: str | Path,
    required_columns: list[str] | tuple[str, ...] | None = None,
    schema: DataSchema | None = None,
) -> PipelineResult:
    """Run extraction, profiling, cleaning, and loud validation."""

    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    source = DataSource(
        name=source_path.stem,
        type=_source_type(source_path),
        path=str(source_path),
    )
    extracted = get_extractor(source.type).extract(source)
    raw_df = extracted.dataframe.copy()
    profile_before = profile_df(raw_df)
    cleaner = Cleaner()
    clean_df, change_log = cleaner.clean(raw_df)
    profile_after = profile_df(clean_df)
    active_schema = schema or (
        DataSchema(required_columns=list(required_columns))
        if required_columns is not None
        else DataSchema()
    )
    try:
        validation_report = validate_df(clean_df, schema=active_schema)
    except ValidationError as error:
        log(
            "bot",
            "data_validation_failed",
            path=str(source_path),
            errors=error.errors,
        )
        raise

    log(
        "bot",
        "data_pipeline_completed",
        path=str(source_path),
        raw_rows=len(raw_df),
        clean_rows=len(clean_df),
        changes=len(change_log),
    )
    return PipelineResult(
        raw_df=raw_df,
        clean_df=clean_df,
        profile_before=profile_before,
        profile_after=profile_after,
        change_log=change_log,
        validation_report=validation_report,
    )
