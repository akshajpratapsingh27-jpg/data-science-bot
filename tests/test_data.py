"""Data pipeline tests - exactly three tests as required."""

from pathlib import Path

import pandas as pd
import pytest

from dsbot.data.pipeline import run
from dsbot.data.validate import DEFAULT_REQUIRED_COLUMNS, ValidationError, validate_df

CSV_PATH = Path(__file__).parents[1] / "data" / "samples" / "dirty_sales.csv"


def test_duplicates_and_mixed_dates_are_fixed_and_logged():
    result = run(CSV_PATH)

    assert len(result.raw_df) == 9
    assert len(result.clean_df) == 8
    assert result.profile_before["duplicate_count"] == 1
    assert result.profile_after["duplicate_count"] == 0
    assert pd.api.types.is_datetime64_any_dtype(result.clean_df["sale_date"])
    assert (
        result.clean_df["sale_date"]
        .dropna()
        .dt.strftime("%Y-%m-%d")
        .str.fullmatch(r"\d{4}-\d{2}-\d{2}")
        .all()
    )
    duplicate_change = next(
        change for change in result.change_log if change.step == "drop_exact_duplicates"
    )
    assert duplicate_change.rows_affected == 1


def test_outliers_are_flagged_without_reducing_row_count():
    result = run(CSV_PATH)

    assert len(result.clean_df) == 8
    assert "is_outlier" in result.clean_df.columns
    assert result.clean_df.loc[result.clean_df["sale_id"].eq("S-005"), "is_outlier"].all()
    outlier_change = next(change for change in result.change_log if change.step == "flag_outliers")
    assert outlier_change.rows_affected >= 1
    assert outlier_change.details["deleted"] is False


def test_validation_fails_loudly_on_missing_required_column():
    dataframe = pd.read_csv(CSV_PATH).drop(columns=["region"])

    with pytest.raises(ValidationError, match="region"):
        validate_df(dataframe, required_columns=DEFAULT_REQUIRED_COLUMNS)
