"""Explicit data validation with loud, structured failures."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, Field


DEFAULT_REQUIRED_COLUMNS = ("sale_id", "sale_date", "category", "amount", "region")


class DataSchema(BaseModel):
    """Customizable validation schema."""

    required_columns: list[str] = Field(default_factory=lambda: list(DEFAULT_REQUIRED_COLUMNS))
    column_types: dict[str, str] = Field(
        default_factory=lambda: {
            "sale_id": "string",
            "sale_date": "date",
            "category": "string",
            "amount": "numeric",
            "region": "string",
        }
    )
    ranges: dict[str, tuple[float | None, float | None]] = Field(default_factory=dict)
    unique_columns: list[str] = Field(default_factory=lambda: ["sale_id"])


class ValidationReport(BaseModel):
    """Successful validation details returned by ``validate_df``."""

    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)


class ValidationError(ValueError):
    """Raised when a dataframe fails its declared schema."""

    def __init__(self, errors: list[str] | str):
        messages = [errors] if isinstance(errors, str) else errors
        super().__init__("Data validation failed: " + "; ".join(messages))
        self.errors = messages


def _type_error(df: pd.DataFrame, column: str, expected: str) -> str | None:
    if column not in df.columns:
        return None
    series = df[column]
    if expected == "numeric" and not pd.api.types.is_numeric_dtype(series):
        return f"Column '{column}' must be numeric"
    if expected == "date" and not (
        pd.api.types.is_datetime64_any_dtype(series)
        or pd.to_datetime(series, format="mixed", errors="coerce").notna().all()
    ):
        return f"Column '{column}' must contain dates"
    if expected == "string" and not (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
        or pd.api.types.is_categorical_dtype(series)
    ):
        return f"Column '{column}' must contain strings"
    return None


def validate_df(
    df: pd.DataFrame,
    required_columns: list[str] | tuple[str, ...] | None = None,
    schema: DataSchema | None = None,
) -> dict[str, Any]:
    """Validate required columns, types, ranges, and uniqueness.

    Any failure raises ``ValidationError``; failures are never converted into a
    successful report.
    """

    active_schema = schema or DataSchema(
        required_columns=list(required_columns or DEFAULT_REQUIRED_COLUMNS)
    )
    errors: list[str] = []
    missing = [column for column in active_schema.required_columns if column not in df.columns]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")

    type_checks: dict[str, str] = {}
    for column, expected in active_schema.column_types.items():
        error = _type_error(df, column, expected)
        if error:
            errors.append(error)
        elif column in df.columns:
            type_checks[column] = expected

    range_checks: dict[str, dict[str, float | None]] = {}
    for column, (lower, upper) in active_schema.ranges.items():
        if column not in df.columns:
            continue
        if not pd.api.types.is_numeric_dtype(df[column]):
            errors.append(f"Range check for '{column}' requires a numeric column")
            continue
        if lower is not None and (df[column] < lower).fillna(False).any():
            errors.append(f"Column '{column}' contains values below {lower}")
        if upper is not None and (df[column] > upper).fillna(False).any():
            errors.append(f"Column '{column}' contains values above {upper}")
        range_checks[column] = {"min": lower, "max": upper}

    uniqueness_checks: dict[str, int] = {}
    for column in active_schema.unique_columns:
        if column not in df.columns:
            continue
        duplicate_count = int(df[column].duplicated(keep=False).sum())
        uniqueness_checks[column] = duplicate_count
        if duplicate_count:
            errors.append(f"Column '{column}' must be unique; {duplicate_count} duplicate values found")

    report = ValidationReport(
        valid=not errors,
        errors=errors,
        checks={
            "required_columns": active_schema.required_columns,
            "types": type_checks,
            "ranges": range_checks,
            "uniqueness": uniqueness_checks,
        },
    )
    if errors:
        raise ValidationError(errors)
    return report.model_dump()


validate = validate_df
