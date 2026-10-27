"""Correlations. The TestAnalyzerParity cases are ported from the Data Quality
Analyzer's TestCorrelations."""

import numpy as np
import pandas as pd

from data_quality_mcp.profiler_core.correlations import compute_correlations, cramers_v
from data_quality_mcp.profiler_core.type_detector import detect_types


def _correlations(df: pd.DataFrame, **kwargs) -> dict:
    return compute_correlations(df, detect_types(df), **kwargs)


class TestAnalyzerParity:
    def test_pearson_computed_for_numeric(self):
        rng = np.random.default_rng(42)
        x = rng.normal(0, 1, 100)
        result = _correlations(pd.DataFrame({"x": x, "y": x + rng.normal(0, 0.1, 100)}))
        assert abs(result["pearson"]["x"]["y"]) > 0.9

    def test_high_correlation_pair_detected(self):
        x = np.random.default_rng(0).normal(0, 1, 100)
        result = _correlations(pd.DataFrame({"x": x, "y": x + 1e-6}))
        assert len(result["high_correlation_pairs"]) > 0

    def test_pearson_empty_without_numeric(self):
        result = _correlations(pd.DataFrame({"cat": ["a", "b", "a", "b"] * 5}))
        assert result["pearson"] == {}

    def test_cramers_v_for_categorical(self):
        df = pd.DataFrame({"a": ["x", "y"] * 10, "b": ["x", "y"] * 10})
        assert _correlations(df)["cramers_v"]["a"]["b"] >= 0.9

    def test_schema_keys_present(self, messy_df: pd.DataFrame):
        result = _correlations(messy_df)
        assert set(result) == {"pearson", "cramers_v", "high_correlation_pairs"}

    def test_high_pairs_sorted_by_abs_value(self):
        x = np.random.default_rng(1).normal(0, 1, 100)
        df = pd.DataFrame({"a": x, "b": x + 1e-6, "c": -x * 2 + 1e-6})
        values = [abs(p["value"]) for p in _correlations(df)["high_correlation_pairs"]]
        assert values == sorted(values, reverse=True)


def test_negative_correlation_is_a_high_pair():
    x = np.arange(50, dtype=float)
    pairs = _correlations(pd.DataFrame({"a": x, "b": -x}))["high_correlation_pairs"]
    assert pairs == [{"col_a": "a", "col_b": "b", "method": "pearson", "value": -1.0}]


def test_constant_column_correlation_is_null():
    df = pd.DataFrame({"a": np.arange(10, dtype=float), "b": [1.0] * 10})
    result = _correlations(df)
    assert result["pearson"]["a"]["b"] is None
    assert result["high_correlation_pairs"] == []


def test_numeric_column_limit():
    df = pd.DataFrame(
        {c: np.arange(10, dtype=float) * (i + 1) for i, c in enumerate("abc")}
    )
    result = _correlations(df, max_numeric_columns=2)
    assert set(result["pearson"]) == {"a", "b"}


def test_high_cardinality_categorical_skipped():
    df = pd.DataFrame(
        {
            "low": ["x", "y"] * 30,
            "low2": ["x", "y"] * 30,
            "many": [f"v{i % 30}" for i in range(60)],  # 30 distinct values
        }
    )
    result = _correlations(df, max_cramers_cardinality=10)
    # As in the analyzer, every included column gets an entry (upper triangle)
    assert set(result["cramers_v"]) == {"low", "low2"}
    assert "many" not in result["cramers_v"]["low"]


def test_categorical_column_limit():
    df = pd.DataFrame({c: ["x", "y"] * 10 for c in "abcd"})
    assert set(_correlations(df, max_cramers_columns=2)["cramers_v"]) == {"a", "b"}


def test_cramers_v_degenerate_table_is_zero():
    assert cramers_v(pd.Series(["x"] * 5), pd.Series(["a", "b", "a", "b", "a"])) == 0.0


# --- minimum shared rows (planning/milestone-3.5.md, D5) ---


def _paired(n_shared: int) -> pd.DataFrame:
    """x and y both present on n_shared rows (y = 2x), y missing on 5 more."""
    x = [float(i) for i in range(n_shared + 5)]
    y = [2.0 * i for i in range(n_shared)] + [None] * 5
    return pd.DataFrame({"x": x, "y": y})


def test_pearson_needs_ten_shared_rows():
    below = _correlations(_paired(9))
    assert below["pearson"]["x"]["y"] is None
    assert below["high_correlation_pairs"] == []
    at = _correlations(_paired(10))
    assert at["pearson"]["x"]["y"] == 1.0
    assert at["high_correlation_pairs"][0]["value"] == 1.0


def test_cramers_v_needs_ten_shared_rows():
    def frame(n_shared: int) -> pd.DataFrame:
        a = ["x", "y"] * 10
        b = (["x", "y"] * 10)[:n_shared] + [None] * (20 - n_shared)
        return pd.DataFrame({"a": a, "b": b})

    below = _correlations(frame(9))
    assert below["cramers_v"]["a"]["b"] is None
    assert below["high_correlation_pairs"] == []
    # Computed from 10 rows on (Yates-corrected on a 2x2 table, as in the analyzer)
    assert _correlations(frame(10))["cramers_v"]["a"]["b"] > 0.5


def test_two_rows_no_longer_give_a_perfect_correlation():
    # The 4-row inline CSV from M3's manual testing: id and price share 2 rows
    df = pd.DataFrame(
        {
            "id": [1, 2, 2, 3],
            "name": ["a", "b", "b", "c"],
            "price": [1.5, None, None, 99],
        }
    )
    result = _correlations(df)
    assert result["pearson"]["id"]["price"] is None
    assert result["high_correlation_pairs"] == []
