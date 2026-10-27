"""Possible sentinel values among the outliers (planning/milestone-3.5.md, D2)."""

import numpy as np
import pandas as pd

from data_quality_mcp.profiler_core.outliers import detect_outliers
from data_quality_mcp.profiler_core.report import prepare_frame
from data_quality_mcp.profiler_core.type_detector import detect_types


def _sentinels(df: pd.DataFrame) -> dict:
    """Sentinels per column. A single-column frame gets a row id, as real data
    has other columns: otherwise rows sharing a value are exact duplicates."""
    if len(df.columns) == 1:
        df = df.assign(row=range(len(df)))
    df = prepare_frame(df, "file_path")
    return {
        col: None if info is None else info["possible_sentinels"]
        for col, info in detect_outliers(df, detect_types(df)).items()
    }


def _continuous(n: int = 200, seed: int = 0) -> list[float]:
    """Many distinct values, as in a real measurement column."""
    return list(np.random.default_rng(seed).normal(100, 10, n).round(2))


def test_fixture_lists_exactly_the_injected_placeholders(messy_df: pd.DataFrame):
    result = _sentinels(messy_df)
    assert result["price"] == [
        {"value": -1000.0, "count": 4},
        {"value": 5000.0, "count": 4},
        {"value": 9999999.0, "count": 2},
    ]
    assert all(v in ([], None) for col, v in result.items() if col != "price")


def test_without_other_columns_repeats_are_duplicate_rows():
    df = prepare_frame(pd.DataFrame({"x": _continuous() + [-1.0, -1.0]}), "file_path")
    assert detect_outliers(df, detect_types(df))["x"]["possible_sentinels"] == []


def test_repeated_extreme_value_is_listed():
    values = _continuous() + [-1.0, -1.0, -1.0]
    assert _sentinels(pd.DataFrame({"x": values}))["x"] == [{"value": -1.0, "count": 3}]


def test_repeats_in_a_column_with_few_distinct_values_are_not_listed():
    # A count column repeats by nature: the unique-ratio gate skips it
    values = [1, 2, 2, 3, 3, 3, 4, 4, 5] * 20 + [40, 40, 40]
    assert _sentinels(pd.DataFrame({"x": values}))["x"] == []


def test_an_exact_duplicate_row_does_not_create_a_candidate():
    df = pd.DataFrame({"x": _continuous() + [-50.0], "y": list(range(201))})
    df = pd.concat([df, df.iloc[[-1]]], ignore_index=True)  # duplicate that row
    assert _sentinels(df)["x"] == []


def test_a_value_once_plus_its_duplicate_row_has_count_two_but_is_no_candidate():
    df = pd.DataFrame({"x": _continuous() + [9999.0], "y": list(range(201))})
    df = pd.concat([df, df.iloc[[-1]]], ignore_index=True)
    # 9999 is still listed, through the all-9s test, with both rows counted
    assert _sentinels(df)["x"] == [{"value": 9999.0, "count": 2}]
    df.loc[df["x"] == 9999.0, "x"] = -50.0  # not all 9s: the repeat test decides
    assert _sentinels(df)["x"] == []


def test_a_single_all_nines_value_is_listed():
    for code in (999.0, 9999.0, -9999.0):
        assert _sentinels(pd.DataFrame({"x": _continuous() + [code]}))["x"] == [
            {"value": code, "count": 1}
        ]


def test_a_code_inside_the_fences_is_not_listed():
    values = _continuous() + [99.0, 99.0, 99.0]  # inside the normal range
    assert _sentinels(pd.DataFrame({"x": values}))["x"] == []


def test_infinite_values_are_never_candidates():
    values = _continuous() + [np.inf, np.inf, -np.inf, -np.inf]
    assert _sentinels(pd.DataFrame({"x": values}))["x"] == []


def test_no_fences_gives_an_empty_list():
    # Fewer than 4 finite values: only the infinite ones are flagged, no fences
    assert _sentinels(pd.DataFrame({"x": [1.0, 2.0, np.inf]}))["x"] == []
    # IQR of 0: no fences either
    assert _sentinels(pd.DataFrame({"x": [5.0] * 20 + [999.0]}))["x"] == []


def test_documented_limit_legitimate_repeated_extremes_are_listed():
    # Real prices 1..100 and two real sales at 1000: listed, as the docs warn
    values = [float(v) for v in range(1, 101)] + [1000.0, 1000.0]
    assert _sentinels(pd.DataFrame({"x": values}))["x"] == [
        {"value": 1000.0, "count": 2}
    ]


def test_mixed_columns_use_their_numeric_values():
    values = [str(v) for v in _continuous()] + ["TBD", "-1", "-1"]
    assert _sentinels(pd.DataFrame({"x": values}))["x"] == [{"value": -1.0, "count": 2}]


def test_sorted_by_count_then_value_and_capped():
    codes = []
    for i in range(12):
        codes += [-1000.0 - i] * (2 + i % 3)
    result = _sentinels(pd.DataFrame({"x": _continuous(400) + codes}))["x"]
    assert len(result) == 10
    keys = [(-s["count"], s["value"]) for s in result]
    assert keys == sorted(keys)
