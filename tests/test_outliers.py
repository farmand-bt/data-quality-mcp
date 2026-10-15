"""Outlier detection. The TestAnalyzerParity cases are ported from the Data
Quality Analyzer's TestOutliers, adapted only for listed deviations."""

import numpy as np
import pandas as pd
import pytest

from data_quality_mcp.profiler_core.outliers import (
    detect_column_outliers,
    detect_outliers,
    outlier_score_pct,
)
from data_quality_mcp.profiler_core.type_detector import detect_types


def _outliers(df: pd.DataFrame, **kwargs) -> dict:
    return detect_outliers(df, detect_types(df), **kwargs)


class TestAnalyzerParity:
    def test_iqr_outlier_detected(self):
        df = pd.DataFrame({"x": list(range(1, 21)) + [1000.0]})
        result = _outliers(df)
        assert result["x"]["iqr_count"] > 0
        assert len(result["x"]["iqr_indices"]) > 0

    def test_no_outliers_uniform_data(self):
        result = _outliers(pd.DataFrame({"x": list(range(1, 21))}))
        assert result["x"]["iqr_count"] == 0

    def test_zscore_outlier_detected(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({"x": list(rng.normal(0, 1, 100)) + [15.0]})
        assert _outliers(df)["x"]["zscore_count"] > 0

    def test_non_numeric_is_none(self):
        # Deviation: the analyzer returns zero counts; here None = not applicable
        result = _outliers(pd.DataFrame({"cat": ["a", "b", "c"] * 5}))
        assert result["cat"] is None

    def test_constant_column_no_outliers(self):
        result = _outliers(pd.DataFrame({"x": [5.0] * 10}))
        assert result["x"]["iqr_count"] == 0
        assert result["x"]["zscore_count"] == 0

    def test_too_few_values_is_none(self):
        # Deviation: the analyzer returns zero counts below 4 values
        assert _outliers(pd.DataFrame({"x": [1.0, 2.0, 3.0]}))["x"] is None

    def test_indices_are_valid_row_labels(self):
        df = pd.DataFrame({"x": [1.0] * 10 + [999.0]})
        for idx in _outliers(df)["x"]["iqr_indices"]:
            assert idx in df.index

    def test_pct_is_proportion_of_total_rows(self):
        df = pd.DataFrame({"x": list(range(1, 11)) + [999.0]})
        assert _outliers(df)["x"]["iqr_pct"] == pytest.approx(1 / 11 * 100, abs=0.5)

    def test_all_columns_covered(self, messy_df: pd.DataFrame):
        assert set(_outliers(messy_df)) == set(messy_df.columns)


def test_iqr_bounds_reported():
    result = _outliers(pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]}))["x"]
    assert result["iqr_lower_bound"] < 1.0 < result["iqr_upper_bound"] < 100.0
    assert result["iqr_indices"] == [5]


def test_constant_column_has_no_bounds():
    result = _outliers(pd.DataFrame({"x": [5.0] * 10}))["x"]
    assert result["iqr_lower_bound"] is None
    assert result["iqr_upper_bound"] is None


def test_all_nan_numeric_column_is_none():
    assert _outliers(pd.DataFrame({"x": [np.nan] * 10}))["x"] is None


def test_mixed_column_ignores_non_numeric_values():
    df = pd.DataFrame({"x": ["1", "2", "3", "4", "5", "6", "7", "1000", "n/a"]})
    result = _outliers(df)["x"]
    assert result["iqr_indices"] == [7]


def test_indices_are_capped_and_flagged():
    values = [1.0] * 20 + [2.0] * 20 + [1000.0] * 10
    result = detect_column_outliers(pd.Series(values), 50, max_indices=3)
    assert result["iqr_count"] == 10  # counts stay exact
    assert result["iqr_indices"] == [40, 41, 42]  # sorted, first 3
    assert result["indices_truncated"] is True


def test_not_truncated_below_cap():
    result = _outliers(pd.DataFrame({"x": list(range(1, 21)) + [1000.0]}))["x"]
    assert result["indices_truncated"] is False


def test_score_pct_averages_columns_with_outliers():
    outliers = {
        "a": {"iqr_count": 2, "iqr_pct": 4.0},
        "b": {"iqr_count": 1, "iqr_pct": 2.0},
        "c": {"iqr_count": 0, "iqr_pct": 0.0},  # no outliers: excluded
        "d": None,  # not applicable: excluded
    }
    assert outlier_score_pct(outliers) == 3.0
    assert outlier_score_pct({"d": None}) == 0.0


# --- infinite values (deviation: computed on finite values, inf always flagged) ---


def test_fences_ignore_infinite_values():
    # 6 of 10 values are infinite: computed on all values, the quartiles (and
    # so the fences) would be infinite. Here they come from the finite 1..4.
    values = [1.0, 2.0, 3.0, 4.0] + [np.inf] * 3 + [-np.inf] * 3
    result = detect_column_outliers(pd.Series(values), len(values))
    assert result["iqr_lower_bound"] == -0.5
    assert result["iqr_upper_bound"] == 5.5
    assert result["iqr_indices"] == [4, 5, 6, 7, 8, 9]
    assert result["zscore_indices"] == [4, 5, 6, 7, 8, 9]


def test_infinite_values_flagged_even_when_iqr_is_zero():
    values = [5.0] * 8 + [np.inf]
    result = detect_column_outliers(pd.Series(values), len(values))
    assert result["iqr_lower_bound"] is None
    assert result["iqr_indices"] == [8]
    assert result["zscore_indices"] == [8]


def test_fewer_than_four_finite_values_still_flags_infinite():
    values = [1.0, 2.0, 3.0, np.inf, -np.inf]
    result = detect_column_outliers(pd.Series(values), len(values))
    assert (result["iqr_lower_bound"], result["iqr_upper_bound"]) == (None, None)
    assert result["iqr_indices"] == result["zscore_indices"] == [3, 4]
    assert result["iqr_count"] == result["zscore_count"] == 2


def test_fewer_than_four_finite_values_without_inf_is_none():
    assert detect_column_outliers(pd.Series([1.0, 2.0, 3.0]), 3) is None


def test_fences_drop_float_noise():
    # Exact fences here are -0.14999999999999994 and 0.8499999999999999
    result = _outliers(pd.DataFrame({"x": [0.1, 0.2, 0.3, 0.4, 0.5, 9.0]}))["x"]
    assert (result["iqr_lower_bound"], result["iqr_upper_bound"]) == (-0.15, 0.85)
    assert result["iqr_indices"] == [5]


def test_small_scale_fences_keep_precision():
    # Fixed 4-decimal rounding would report these fences as -0.0 and 0.0001
    values = [1e-5, 2e-5, 3e-5, 4e-5, 5e-5, 9e-4]
    result = _outliers(pd.DataFrame({"x": values}))["x"]
    assert result["iqr_lower_bound"] == -1.5e-05
    assert result["iqr_upper_bound"] == 8.5e-05
    assert result["iqr_indices"] == [5]
