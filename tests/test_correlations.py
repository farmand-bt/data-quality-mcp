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
