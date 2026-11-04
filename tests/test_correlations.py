"""Correlations. The TestAnalyzerParity cases are ported from the Data Quality
Analyzer's TestCorrelations."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import chi2_contingency

from data_quality_mcp.profiler_core.correlations import (
    compute_correlations,
    corrected_cramers_v,
    cramers_v,
)
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
        a = [True, False] * 10
        b = ([True, False] * 10)[:n_shared] + [None] * (20 - n_shared)
        return pd.DataFrame({"a": a, "b": pd.Series(b, dtype=object)})

    below = _correlations(frame(9))  # 20 rows, but only 9 shared
    assert below["cramers_v"]["a"]["b"] is None
    assert below["high_correlation_pairs"] == []
    # From 10 shared rows on. Without Yates' correction a matching pair is 1.0
    # (it was 0.8 with it), so it is a high pair (milestone-3.5.md, Amendment)
    at = _correlations(frame(10))
    assert at["cramers_v"]["a"]["b"] == 1.0
    assert at["high_correlation_pairs"][0]["method"] == "cramers_v"


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


# --- bias-corrected Cramér's V (planning/milestone-3.5.md, Amendment, 0.4.3) ---


def _categorical(df: pd.DataFrame, **kwargs) -> dict:
    """compute_correlations with every column typed categorical: on few rows,
    type detection would call a many-level column text."""
    types = {c: {"inferred_type": "categorical"} for c in df.columns}
    return compute_correlations(df, types, **kwargs)


def _plain_v(x: pd.Series, y: pd.Series) -> float:
    """The analyzer's plain V (Yates on 2 x 2), the value before 0.4.3."""
    table = pd.crosstab(x, y)
    chi2 = chi2_contingency(table)[0]
    return float(np.sqrt(chi2 / (table.to_numpy().sum() * (min(table.shape) - 1))))


def _reference_v(x: pd.Series, y: pd.Series) -> float:
    """The corrected V from a pd.crosstab table, no Yates' correction."""
    table = pd.crosstab(x, y)  # drops rows where either value is missing
    if min(table.shape) < 2:
        return 0.0
    chi2 = chi2_contingency(table, correction=False)[0]
    return corrected_cramers_v(float(chi2), int(table.to_numpy().sum()), table.shape)


def test_unrelated_many_level_columns_are_no_longer_a_high_pair():
    rng = np.random.default_rng(3)
    df = pd.DataFrame(
        {
            "a": [f"a{v}" for v in rng.integers(0, 50, 20)],
            "b": [f"b{v}" for v in rng.integers(0, 50, 20)],
        }
    )
    assert _plain_v(df["a"], df["b"]) >= 0.9  # the false high pair before
    result = _categorical(df)
    assert result["cramers_v"]["a"]["b"] < 0.5
    assert result["high_correlation_pairs"] == []


@pytest.mark.parametrize("levels", [2, 5])
def test_a_one_to_one_association_is_one(levels):
    a = [f"x{i % levels}" for i in range(50)]
    b = [f"y{i % levels}" for i in range(50)]
    assert cramers_v(pd.Series(a), pd.Series(b)) == 1.0


@pytest.mark.parametrize(("rows", "high"), [(20, False), (100, True)])
def test_a_many_to_one_mapping_needs_rows_per_level(rows, high):
    # The trade-off the Amendment accepts: 10 levels -> 2 groups is redundant,
    # but on 2 rows per level the corrected V stays below 0.9
    rng = np.random.default_rng(10)
    codes = rng.integers(0, 10, rows)
    df = pd.DataFrame({"city": [f"c{v}" for v in codes], "region": codes % 2})
    result = _categorical(df)
    assert (result["cramers_v"]["city"]["region"] >= 0.9) is high
    assert bool(result["high_correlation_pairs"]) is high


def test_the_fixture_pair(messy_df: pd.DataFrame):
    assert _correlations(messy_df)["cramers_v"] == {
        "neighborhood": {"garage": 0.0292},
        "garage": {},
    }


def test_a_two_by_two_table_has_no_yates_correction():
    rng = np.random.default_rng(22)
    x = pd.Series(rng.integers(0, 2, 30))
    y = pd.Series(np.where(rng.random(30) < 0.7, x, 1 - x))
    table = pd.crosstab(x, y)
    chi2 = chi2_contingency(table, correction=False)[0]
    assert cramers_v(x, y) == corrected_cramers_v(float(chi2), 30, (2, 2))
    assert chi2_contingency(table)[0] < chi2  # Yates would have lowered it


@pytest.mark.parametrize("seed", range(8))
def test_the_numpy_table_matches_crosstab(seed):
    rng = np.random.default_rng(seed)
    n = 200
    x = pd.Series([f"x{v}" for v in rng.integers(0, 6, n)], dtype=object)
    y = pd.Series([[True, False, "maybe"][v] for v in rng.integers(0, 3, n)])
    y = y.astype(object)  # real bools beside a string
    nulls = [None, np.nan, pd.NA, pd.NaT]
    for s in (x, y):
        for i in np.flatnonzero(rng.random(n) < 0.15):
            s[i] = nulls[i % len(nulls)]
    # A category that only appears where the other column is missing
    lonely = int(np.flatnonzero(y.isna().to_numpy())[0])
    x[lonely] = "only-here"
    assert cramers_v(x, y) == pytest.approx(_reference_v(x, y), abs=1e-12)
    assert cramers_v(y, x) == pytest.approx(_reference_v(x, y), abs=1e-12)


def test_series_are_matched_by_label_and_no_shared_rows_gives_zero():
    rng = np.random.default_rng(5)
    x = pd.Series(["a", "b"] * 15)
    y = pd.Series(["p", "q"] * 15, index=rng.permutation(30))
    positional = pd.Series(y.to_numpy())
    assert cramers_v(x, positional) == 1.0  # a different answer by position
    assert cramers_v(x, y) == pytest.approx(_reference_v(x, y), abs=1e-12)
    assert cramers_v(x, y) < 1.0
    assert cramers_v(pd.Series(["a", None]), pd.Series([None, "b"])) == 0.0


def test_a_different_value_on_every_shared_row_gives_zero():
    x = pd.Series([f"r{i}" for i in range(12)])
    y = pd.Series(["a", "b"] * 6)
    assert cramers_v(x, y) == 0.0


def test_one_category_on_the_shared_rows_gives_zero():
    # "b" exists, but only where y is missing
    x = pd.Series(["a"] * 10 + ["b"] * 3)
    y = pd.Series(["p", "q"] * 5 + [None] * 3)
    assert cramers_v(x, y) == 0.0
    df = pd.DataFrame({"x": x, "y": y})
    assert _categorical(df)["cramers_v"]["x"]["y"] == 0.0
