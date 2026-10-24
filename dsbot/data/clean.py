"""Small, auditable pandas cleaning steps."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from dsbot.data.extract import ExtractionResult


class ChangeRecord(BaseModel):
    """One entry in the cleaning Change Log."""

    step: str
    rows_affected: int = 0
    columns_affected: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


CleanStep = Callable[[pd.DataFrame], tuple[pd.DataFrame, ChangeRecord]]


class CleaningStep(BaseModel):
    """Backward-compatible configurable cleaning step."""

    name: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class CleaningPlan(BaseModel):
    """Backward-compatible collection of cleaning steps."""

    steps: list[CleaningStep] = Field(default_factory=list)


def standardize_column_names(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
    cleaned = df.copy()
    original = [str(column) for column in cleaned.columns]
    standardized = [
        str(column).strip().lower().replace(" ", "_").replace("-", "_")
        for column in cleaned.columns
    ]
    cleaned.columns = standardized
    changed = [new for old, new in zip(original, standardized) if old != new]
    return cleaned, ChangeRecord(
        step="standardize_column_names",
        columns_affected=changed,
        details={"before": original, "after": standardized},
    )


def strip_whitespace(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
    cleaned = df.copy()
    changed_rows: set[Any] = set()
    columns: list[str] = []
    for column in cleaned.select_dtypes(include=["object", "string"]).columns:
        original = cleaned[column].copy()
        cleaned[column] = cleaned[column].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )
        changed = original.ne(cleaned[column]) & original.notna()
        if changed.any():
            columns.append(str(column))
            changed_rows.update(cleaned.index[changed])
    return cleaned, ChangeRecord(
        step="strip_whitespace",
        rows_affected=len(changed_rows),
        columns_affected=columns,
    )


def normalize_category_casing(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
    cleaned = df.copy()
    changed_rows: set[Any] = set()
    columns: list[str] = []
    for column in cleaned.columns:
        if "categor" not in str(column).lower():
            continue
        original = cleaned[column].copy()
        cleaned[column] = cleaned[column].map(
            lambda value: value.strip().title() if isinstance(value, str) else value
        )
        changed = original.ne(cleaned[column]) & original.notna()
        if changed.any():
            columns.append(str(column))
            changed_rows.update(cleaned.index[changed])
    return cleaned, ChangeRecord(
        step="normalize_category_casing",
        rows_affected=len(changed_rows),
        columns_affected=columns,
    )


def parse_dates_to_iso(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
    cleaned = df.copy()
    changed_rows: set[Any] = set()
    columns: list[str] = []
    invalid_dates: dict[str, int] = {}
    for column in cleaned.select_dtypes(include=["object", "string"]).columns:
        if "date" not in str(column).lower() and "time" not in str(column).lower():
            continue
        original = cleaned[column].copy()
        parsed = pd.to_datetime(original, format="mixed", errors="coerce")
        cleaned[column] = parsed
        changed = original.notna() & (parsed.notna() | parsed.isna())
        if changed.any():
            columns.append(str(column))
            changed_rows.update(cleaned.index[changed])
        invalid_count = int((original.notna() & parsed.isna()).sum())
        if invalid_count:
            invalid_dates[str(column)] = invalid_count
    return cleaned, ChangeRecord(
        step="parse_dates_to_iso",
        rows_affected=len(changed_rows),
        columns_affected=columns,
        details={"invalid_dates": invalid_dates},
    )


def drop_exact_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
    cleaned = df.drop_duplicates(keep="first").copy()
    dropped = len(df) - len(cleaned)
    return cleaned, ChangeRecord(
        step="drop_exact_duplicates",
        rows_affected=dropped,
        details={"rows_before": len(df), "rows_after": len(cleaned)},
    )


def impute_nulls(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
    cleaned = df.copy()
    changed_rows: set[Any] = set()
    numeric_columns: list[str] = []
    categorical_columns: list[str] = []
    for column in cleaned.select_dtypes(include=[np.number]).columns:
        missing = cleaned[column].isna()
        if not missing.any():
            continue
        median = cleaned[column].median()
        cleaned.loc[missing, column] = median
        changed_rows.update(cleaned.index[missing])
        numeric_columns.append(str(column))
    for column in cleaned.select_dtypes(include=["object", "string", "category"]).columns:
        missing = cleaned[column].isna()
        if not missing.any():
            continue
        cleaned.loc[missing, column] = "Unknown"
        changed_rows.update(cleaned.index[missing])
        categorical_columns.append(str(column))
    return cleaned, ChangeRecord(
        step="impute_nulls",
        rows_affected=len(changed_rows),
        columns_affected=numeric_columns + categorical_columns,
        details={
            "numeric_median_columns": numeric_columns,
            "categorical_unknown_columns": categorical_columns,
        },
    )


def flag_outliers(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
    cleaned = df.copy()
    flags = pd.Series(False, index=cleaned.index, dtype=bool)
    flagged_columns: list[str] = []
    for column in cleaned.select_dtypes(include=[np.number]).columns:
        if str(column) == "is_outlier":
            continue
        series = cleaned[column]
        if series.dropna().empty:
            continue
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        column_flags = (series < lower) | (series > upper)
        if column_flags.any():
            flagged_columns.append(str(column))
            flags = flags | column_flags.fillna(False)
    cleaned["is_outlier"] = flags
    return cleaned, ChangeRecord(
        step="flag_outliers",
        rows_affected=int(flags.sum()),
        columns_affected=flagged_columns,
        details={"outlier_column": "is_outlier", "deleted": False},
    )


DEFAULT_STEPS: tuple[CleanStep, ...] = (
    standardize_column_names,
    strip_whitespace,
    normalize_category_casing,
    parse_dates_to_iso,
    drop_exact_duplicates,
    impute_nulls,
    flag_outliers,
)


class Cleaner:
    """Apply small cleaning functions and return their Change Log."""

    def __init__(self, steps: list[CleanStep] | tuple[CleanStep, ...] | None = None):
        self.steps = list(steps or DEFAULT_STEPS)

    def clean(self, df: pd.DataFrame) -> tuple[pd.DataFrame, list[ChangeRecord]]:
        cleaned = df.copy()
        change_log: list[ChangeRecord] = []
        for step in self.steps:
            cleaned, change = step(cleaned)
            change_log.append(change)
        return cleaned, change_log


def _legacy_step_function(step: CleaningStep) -> CleanStep:
    """Adapt the original CleaningPlan vocabulary to the new Change Log API."""

    step_type = step.type
    if step_type in {"lowercase_columns", "standardize_column_names"}:
        return standardize_column_names
    if step_type in {"strip_strings", "strip_whitespace"}:
        return strip_whitespace
    if step_type == "normalize_category_casing":
        return normalize_category_casing
    if step_type in {"parse_dates", "parse_dates_to_iso"}:
        return parse_dates_to_iso
    if step_type in {"drop_duplicates", "drop_exact_duplicates"}:
        return drop_exact_duplicates
    if step_type in {"impute_nulls", "fill_nulls"}:
        return impute_nulls
    if step_type in {"flag_outliers", "outliers"}:
        return flag_outliers

    def legacy(df: pd.DataFrame) -> tuple[pd.DataFrame, ChangeRecord]:
        before = df.copy()
        if step_type == "drop_na":
            params = step.params
            result = df.dropna(
                subset=params.get("subset"),
                how=params.get("how", "any"),
                thresh=params.get("thresh"),
            )
        elif step_type == "fill_na":
            params = step.params
            if params.get("value") is not None:
                result = df.fillna(params["value"])
            else:
                result = df.fillna(params.get("method"), limit=params.get("limit"))
        elif step_type == "rename":
            result = df.rename(columns=step.params.get("mapping", {}))
        elif step_type == "convert_type":
            result = df.copy()
            for column, dtype in step.params.get("mapping", {}).items():
                if column in result.columns:
                    result[column] = result[column].astype(dtype)
        elif step_type == "filter":
            query = step.params.get("query")
            result = df.query(query) if query else df.copy()
        elif step_type == "custom" and callable(step.params.get("function")):
            result = step.params["function"](df.copy())
        else:
            raise ValueError(f"Unknown cleaning step type: {step_type}")
        changed = len(before) - len(result)
        return result.copy(), ChangeRecord(step=step.name, rows_affected=max(changed, 0))

    return legacy


class DataCleaner:
    """Backward-compatible adapter returning an ``ExtractionResult``."""

    def __init__(self, plan: CleaningPlan | None = None):
        self.plan = plan
        self._applied_steps: list[str] = []
        self._change_log: list[ChangeRecord] = []

    def clean(self, result: ExtractionResult) -> ExtractionResult:
        steps = (
            list(DEFAULT_STEPS)
            if self.plan is None
            else [_legacy_step_function(step) for step in self.plan.steps]
        )
        cleaner = Cleaner(steps)
        dataframe, self._change_log = cleaner.clean(result.dataframe)
        self._applied_steps = [record.step for record in self._change_log]
        return ExtractionResult(
            source_name=result.source_name,
            dataframe=dataframe,
            metadata={
                **result.metadata,
                "cleaning_steps_applied": self._applied_steps,
                "change_log": [record.model_dump() for record in self._change_log],
                "rows_before": result.rows_extracted,
                "rows_after": len(dataframe),
            },
            rows_extracted=len(dataframe),
            columns=[str(column) for column in dataframe.columns],
        )

    @property
    def applied_steps(self) -> list[str]:
        return self._applied_steps

    @property
    def change_log(self) -> list[ChangeRecord]:
        return self._change_log


def create_default_plan() -> CleaningPlan:
    """Create a plan matching the auditable default cleaning pipeline."""

    return CleaningPlan(
        steps=[
            CleaningStep(name="standardize_column_names", type="standardize_column_names"),
            CleaningStep(name="strip_whitespace", type="strip_whitespace"),
            CleaningStep(name="normalize_category_casing", type="normalize_category_casing"),
            CleaningStep(name="parse_dates_to_iso", type="parse_dates_to_iso"),
            CleaningStep(name="drop_exact_duplicates", type="drop_exact_duplicates"),
            CleaningStep(name="impute_nulls", type="impute_nulls"),
            CleaningStep(name="flag_outliers", type="flag_outliers"),
        ]
    )
