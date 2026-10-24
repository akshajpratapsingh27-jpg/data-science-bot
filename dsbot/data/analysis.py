"""Deterministic pandas analysis facts and chart generation."""

from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path
from typing import Any, TypeAlias

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

Fact: TypeAlias = dict[str, Any]
Facts: TypeAlias = list[Fact]


def _safe_name(value: object) -> str:
    """Return a stable identifier fragment for a dataframe label."""

    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()
    return cleaned or "value"


def _number(value: Any) -> int | float:
    """Convert pandas and numpy scalar values to JSON-friendly numbers."""

    if pd.isna(value):
        return 0.0
    converted = float(value)
    return int(converted) if converted.is_integer() else converted


def _fact(fact_id: str, template: str, value: Any, kind: str) -> Fact:
    return {
        "id": fact_id,
        "statement_template": template,
        "value": value,
        "kind": kind,
    }


def _date_columns(df: pd.DataFrame) -> dict[str, pd.Series]:
    parsed: dict[str, pd.Series] = {}
    for column in df.columns:
        series = df[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed[str(column)] = pd.to_datetime(series, errors="coerce")
            continue
        if "date" not in str(column).lower() and "time" not in str(column).lower():
            continue
        candidate = pd.to_datetime(series, format="mixed", errors="coerce")
        if candidate.notna().any():
            parsed[str(column)] = candidate
    return parsed


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in df.select_dtypes(include="number").columns
        if not pd.api.types.is_bool_dtype(df[column])
    ]


def _categorical_columns(df: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in df.select_dtypes(include=["object", "string", "category"]).columns
    ]


def _add_summary_facts(df: pd.DataFrame, facts: Facts) -> None:
    facts.extend(
        [
            _fact(
                "row_count",
                "The cleaned dataset contains {value} rows.",
                int(len(df)),
                "summary",
            ),
            _fact(
                "column_count",
                "The cleaned dataset contains {value} columns.",
                int(len(df.columns)),
                "summary",
            ),
            _fact(
                "missing_cell_count",
                "The cleaned dataset contains {value} missing cells.",
                int(df.isna().sum().sum()),
                "summary",
            ),
            _fact(
                "duplicate_row_count",
                "The cleaned dataset contains {value} duplicate rows.",
                int(df.duplicated().sum()),
                "summary",
            ),
        ]
    )

    for column in _numeric_columns(df):
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        if series.empty:
            continue
        label = _safe_name(column)
        facts.extend(
            [
                _fact(
                    f"numeric_{label}_mean",
                    f"The mean of {column} is {{value}}.",
                    _number(series.mean()),
                    "summary_statistic",
                ),
                _fact(
                    f"numeric_{label}_median",
                    f"The median of {column} is {{value}}.",
                    _number(series.median()),
                    "summary_statistic",
                ),
                _fact(
                    f"numeric_{label}_min",
                    f"The minimum of {column} is {{value}}.",
                    _number(series.min()),
                    "summary_statistic",
                ),
                _fact(
                    f"numeric_{label}_max",
                    f"The maximum of {column} is {{value}}.",
                    _number(series.max()),
                    "summary_statistic",
                ),
            ]
        )


def _add_category_facts(df: pd.DataFrame, facts: Facts) -> None:
    numeric_columns = _numeric_columns(df)
    for category in _categorical_columns(df):
        counts = df[category].value_counts(dropna=False)
        if counts.empty:
            continue
        top_category = str(counts.index[0])
        bottom_category = str(counts.index[-1])
        label = _safe_name(category)
        facts.extend(
            [
                _fact(
                    f"top_{label}_by_count",
                    f"The most common {category} is {{value}}.",
                    {"category": top_category, "count": int(counts.iloc[0])},
                    "top_category",
                ),
                _fact(
                    f"bottom_{label}_by_count",
                    f"The least common {category} is {{value}}.",
                    {"category": bottom_category, "count": int(counts.iloc[-1])},
                    "bottom_category",
                ),
            ]
        )

        if not 1 < counts.size <= 20:
            continue
        for metric in numeric_columns:
            grouped = df.groupby(category, dropna=False)[metric].mean().dropna()
            if grouped.empty:
                continue
            values = {str(key): _number(value) for key, value in grouped.items()}
            metric_label = _safe_name(metric)
            facts.append(
                _fact(
                    f"group_{label}_{metric_label}_mean",
                    f"The average {metric} by {category} is {{value}}.",
                    values,
                    "group_comparison",
                )
            )
            top_key = grouped.idxmax()
            bottom_key = grouped.idxmin()
            facts.extend(
                [
                    _fact(
                        f"top_{label}_by_{metric_label}",
                        f"The {category} with the highest average {metric} is {{value}}.",
                        {
                            "category": str(top_key),
                            "metric": metric,
                            "value": _number(grouped.loc[top_key]),
                        },
                        "top_category",
                    ),
                    _fact(
                        f"bottom_{label}_by_{metric_label}",
                        f"The {category} with the lowest average {metric} is {{value}}.",
                        {
                            "category": str(bottom_key),
                            "metric": metric,
                            "value": _number(grouped.loc[bottom_key]),
                        },
                        "bottom_category",
                    ),
                ]
            )


def _add_trend_facts(df: pd.DataFrame, facts: Facts) -> None:
    date_columns = _date_columns(df)
    numeric_columns = _numeric_columns(df)
    if not date_columns or not numeric_columns:
        return
    date_column, dates = next(iter(date_columns.items()))
    metric = numeric_columns[0]
    trend = pd.DataFrame({"date": dates, "value": pd.to_numeric(df[metric], errors="coerce")})
    trend = trend.dropna()
    if trend.empty:
        return
    trend["period"] = trend["date"].dt.to_period("M").astype(str)
    grouped = trend.groupby("period", sort=True)["value"].sum()
    values = {str(period): _number(value) for period, value in grouped.items()}
    facts.append(
        _fact(
            f"trend_{_safe_name(date_column)}_{_safe_name(metric)}_sum",
            f"The {metric} time trend by {date_column} is {{value}}.",
            values,
            "time_trend",
        )
    )


def _add_correlation_facts(df: pd.DataFrame, facts: Facts) -> None:
    numeric_columns = _numeric_columns(df)
    for left, right in combinations(numeric_columns, 2):
        pair = df[[left, right]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(pair) < 2 or pair[left].nunique() < 2 or pair[right].nunique() < 2:
            continue
        correlation = float(pair[left].corr(pair[right]))
        if abs(correlation) <= 0.5:
            continue
        left_label = _safe_name(left)
        right_label = _safe_name(right)
        facts.append(
            _fact(
                f"correlation_{left_label}_{right_label}",
                f"The correlation between {left} and {right} is {{value}}.",
                _number(correlation),
                "correlation",
            )
        )
        try:
            from scipy.stats import pearsonr
        except ImportError:
            continue
        statistic, pvalue = pearsonr(pair[left], pair[right])
        facts.append(
            _fact(
                f"pearson_{left_label}_{right_label}",
                f"The Pearson test for {left} and {right} has a p-value of {{value}}.",
                {"statistic": _number(statistic), "pvalue": _number(pvalue)},
                "statistical_test",
            )
        )


def _save_charts(df: pd.DataFrame, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    numeric_columns = _numeric_columns(df)
    category_columns = _categorical_columns(df)
    date_columns = _date_columns(df)

    fig, axis = plt.subplots(figsize=(8, 4.5))
    if numeric_columns:
        means = [pd.to_numeric(df[column], errors="coerce").mean() for column in numeric_columns]
        axis.bar(numeric_columns, means, color="#4472C4")
        axis.set_ylabel("Mean")
        axis.tick_params(axis="x", rotation=30)
        axis.set_title("Numeric column means")
    else:
        axis.text(0.5, 0.5, "No numeric columns", ha="center", va="center")
        axis.set_axis_off()
    fig.tight_layout()
    path = output_dir / "numeric_summary.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    paths.append(path)

    fig, axis = plt.subplots(figsize=(8, 4.5))
    category = next(
        (column for column in category_columns if 0 < df[column].nunique(dropna=False) <= 20),
        None,
    )
    if category is not None:
        counts = df[category].fillna("<missing>").astype(str).value_counts()
        axis.bar(counts.index, counts.values, color="#70AD47")
        axis.set_ylabel("Rows")
        axis.tick_params(axis="x", rotation=30)
        axis.set_title(f"{category} counts")
    else:
        axis.text(0.5, 0.5, "No categorical columns", ha="center", va="center")
        axis.set_axis_off()
    fig.tight_layout()
    path = output_dir / "category_counts.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    paths.append(path)

    fig, axis = plt.subplots(figsize=(8, 4.5))
    if date_columns and numeric_columns:
        date_column, dates = next(iter(date_columns.items()))
        metric = numeric_columns[0]
        trend = pd.DataFrame(
            {"date": dates, "value": pd.to_numeric(df[metric], errors="coerce")}
        ).dropna()
        trend = trend.sort_values("date")
        axis.plot(trend["date"], trend["value"], marker="o", color="#ED7D31")
        axis.set_title(f"{metric} over {date_column}")
        axis.tick_params(axis="x", rotation=30)
    elif len(numeric_columns) >= 2:
        left, right = numeric_columns[:2]
        axis.scatter(df[left], df[right], color="#A5A5A5")
        axis.set_xlabel(left)
        axis.set_ylabel(right)
        axis.set_title(f"{right} versus {left}")
    else:
        axis.text(0.5, 0.5, "No time trend or numeric relationship", ha="center", va="center")
        axis.set_axis_off()
    fig.tight_layout()
    path = output_dir / "time_or_relationship.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    paths.append(path)
    return paths


def analyze(clean_df: pd.DataFrame, charts_dir: str | Path = "outputs/charts") -> Facts:
    """Compute deterministic facts and save three charts for a cleaned dataframe."""

    dataframe = clean_df.copy()
    facts: Facts = []
    _add_summary_facts(dataframe, facts)
    _add_category_facts(dataframe, facts)
    _add_trend_facts(dataframe, facts)
    _add_correlation_facts(dataframe, facts)
    _save_charts(dataframe, Path(charts_dir))
    return facts
