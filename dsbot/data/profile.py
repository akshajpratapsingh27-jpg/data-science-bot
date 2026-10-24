"""Pandas-based data quality profiling."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


DATE_PATTERNS = {
    "iso_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "slash_date": re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
    "year_slash_date": re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$"),
    "long_month_date": re.compile(r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}$"),
}


def _date_format(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    for name, pattern in DATE_PATTERNS.items():
        if pattern.match(stripped):
            return name
    return None


def _numeric_outliers(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for column in df.select_dtypes(include="number").columns:
        series = df[column].dropna()
        if series.empty:
            result[str(column)] = {
                "count": 0,
                "lower_bound": None,
                "upper_bound": None,
                "indices": [],
            }
            continue
        q1 = float(series.quantile(0.25))
        q3 = float(series.quantile(0.75))
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        mask = (df[column] < lower) | (df[column] > upper)
        result[str(column)] = {
            "count": int(mask.fillna(False).sum()),
            "lower_bound": lower,
            "upper_bound": upper,
            "indices": [int(index) for index in df.index[mask.fillna(False)]],
        }
    return result


def _inconsistent_case_categories(df: pd.DataFrame) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    for column in df.select_dtypes(include=["object", "string", "category"]).columns:
        values = df[column].dropna().astype(str).str.strip()
        groups: dict[str, set[str]] = {}
        for value in values:
            groups.setdefault(value.casefold(), set()).add(value)
        inconsistent = {
            key: sorted(variants)
            for key, variants in groups.items()
            if len(variants) > 1
        }
        if inconsistent:
            result[str(column)] = inconsistent
    return result


def _mixed_date_formats(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for column in df.select_dtypes(include=["object", "string"]).columns:
        formats = {
            format_name
            for value in df[column].dropna()
            if (format_name := _date_format(value)) is not None
        }
        if not formats and "date" not in str(column).lower():
            continue
        parsed = pd.to_datetime(df[column], format="mixed", errors="coerce")
        result[str(column)] = {
            "formats": sorted(formats),
            "mixed": len(formats) > 1,
            "parseable_count": int(parsed.notna().sum()),
            "invalid_count": int(parsed.isna().sum() - df[column].isna().sum()),
        }
    return result


def profile_df(df: pd.DataFrame) -> dict[str, Any]:
    """Return row, type, null, duplicate, outlier, and consistency metrics."""

    row_count = len(df)
    null_percent = {
        str(column): (float(df[column].isna().mean() * 100) if row_count else 0.0)
        for column in df.columns
    }
    return {
        "row_count": row_count,
        "column_count": len(df.columns),
        "dtypes": {str(column): str(dtype) for column, dtype in df.dtypes.items()},
        "null_percent": null_percent,
        "duplicate_count": int(df.duplicated().sum()),
        "numeric_outliers": _numeric_outliers(df),
        "inconsistent_case_categories": _inconsistent_case_categories(df),
        "mixed_date_formats": _mixed_date_formats(df),
    }
