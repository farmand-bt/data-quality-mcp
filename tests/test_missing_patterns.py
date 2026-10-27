"""Missing-value associations (planning/milestone-3.5.md, D1)."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import pearsonr

from data_quality_mcp.profiler_core.missing import (
    corrected_cramers_v,
    missing_associations,
)
from data_quality_mcp.profiler_core.report import generate_report, prepare_frame
from data_quality_mcp.profiler_core.type_detector import detect_types


def _associations(df: pd.DataFrame, **kwargs) -> dict:
    df = prepare_frame(df, "file_path")
    return missing_associations(df, detect_types(df), **kwargs)


def test_fixture_figures(messy_df: pd.DataFrame):
    result = _associations(messy_df)
    assert result["school_rating"] == [
        {"column": "age_years", "method": "point_biserial", "value": 0.663}
    ]
    assert result["price"] == [] and result["sqft"] == []  # random gaps
    assert result["id"] is None  # no missing values
    report = generate_report(messy_df, "m.csv", "file_path")
    assert report["columns"]["school_rating"]["missing_related_to"][0]["value"] == 0.663
    messages = [r["message"] for r in report["recommendations"]]
    assert any("missing more often where 'age_years' is higher" in m for m in messages)


def test_random_missingness_lists_nothing():
    # Seeded calibration: independent gaps across sizes and cardinalities
    rng = np.random.default_rng(2024)
    for n in (40, 100, 520):
        for k in (2, 8, 20):
            for _ in range(10):
                df = pd.DataFrame(
                    {
                        **{f"x{i}": rng.normal(size=n) for i in range(6)},
                        **{
                            f"c{i}": rng.integers(0, k, n).astype(str) for i in range(3)
                        },
                    }
                )
                df.loc[rng.random(n) < 0.3, "x0"] = np.nan
                result = _associations(df)
                assert result["x0"] in ([], None), (n, k, result["x0"])


def _driven(n: int = 400, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = rng.integers(0, 80, n).astype(float)
    group = rng.choice(["a", "b", "c", "d"], n)
    df = pd.DataFrame(
        {"age": age, "group": group, "noise": rng.normal(size=n), "target": 1.0}
    )
    return df


def test_point_biserial_finds_the_driving_column_with_its_sign():
    df = _driven()
    df.loc[df["age"] > 60, "target"] = np.nan
    (found,) = _associations(df)["target"]
    assert (found["column"], found["method"]) == ("age", "point_biserial")
    assert found["value"] > 0.5
    df = _driven()
    df.loc[df["age"] < 20, "target"] = np.nan
    assert _associations(df)["target"][0]["value"] < -0.5  # missing where lower


def test_cramers_v_finds_a_driving_category():
    df = _driven()
    df.loc[df["group"] == "a", "target"] = np.nan
    (found,) = _associations(df)["target"]
    assert (found["column"], found["method"]) == ("group", "cramers_v")
    assert found["value"] > 0.5


def test_missing_together():
    df = _driven()
    gaps = df.index % 5 == 0
    df.loc[gaps, "target"] = np.nan
    df.loc[gaps, "noise"] = np.nan
    result = _associations(df)
    assert {"column": "noise", "method": "missing_together", "value": 1.0} in result[
        "target"
    ]


def test_columns_missing_in_complementary_rows_are_not_listed():
    # Negative phi: never "missing together"
    df = _driven()
    df.loc[df.index % 2 == 0, "target"] = np.nan
    df.loc[df.index % 2 == 1, "noise"] = np.nan
    methods = [a["method"] for a in _associations(df)["target"]]
    assert "missing_together" not in methods


def test_a_sparse_comparison_column_is_skipped():
    # Present on 2 rows only: one with the target present, one missing, |r| = 1
    df = _driven(60)
    df.loc[df.index % 2 == 0, "target"] = np.nan
    df["sparse"] = np.nan
    df.loc[0, "sparse"], df.loc[1, "sparse"] = 1.0, 2.0
    assert all(a["column"] != "sparse" for a in _associations(df)["target"])


def test_rare_categories_qualify_no_pair():
    # 48 categories with 2 rows each: none reaches 5 rows, so no Cramér's V
    n = 96
    df = pd.DataFrame({"cat": [f"k{i // 2}" for i in range(n)], "target": 1.0})
    df.loc[df.index % 4 < 2, "target"] = np.nan
    assert _associations(df)["target"] is None


def test_null_means_not_tested_and_empty_means_tested():
    df = pd.DataFrame({"a": range(30), "b": [1.0] * 30})
    assert _associations(df)["b"] is None  # no missing values
    df.loc[:4, "b"] = np.nan  # 5 missing: below the 10 a pair needs
    assert _associations(df)["b"] is None
    df.loc[:14, "b"] = np.nan  # 15 missing, 15 present: tested
    df["a"] = np.random.default_rng(3).normal(size=30)
    assert _associations(df)["b"] == []


def test_columns_past_the_cap_are_not_tested():
    df = _driven()
    df.loc[df["age"] > 60, "target"] = np.nan
    df.loc[df["age"] > 60, "noise"] = np.nan
    result = _associations(df, max_target_columns=1)
    assert result["noise"] is not None and result["target"] is None


def test_text_columns_are_targets_but_not_comparison_columns():
    df = _driven()
    df["note"] = [f"free text {i}" for i in range(len(df))]  # high cardinality
    df.loc[df["age"] > 60, "note"] = None
    assert _associations(df)["note"][0]["column"] == "age"
    df.loc[df["age"] > 60, "target"] = np.nan
    assert all(
        not (a["column"] == "note" and a["method"] != "missing_together")
        for a in _associations(df)["target"]
    )


def test_bonferroni_divides_by_the_pairs_actually_tested():
    rng = np.random.default_rng(5)
    n = 120
    x = rng.normal(size=n)
    target = np.where(x + rng.normal(scale=1.0, size=n) > 0.8, np.nan, 1.0)
    df = pd.DataFrame({"x": x, "target": target})
    r, p = pearsonr(np.isnan(target), x)
    assert abs(r) >= 0.3  # the effect size passes; p decides
    listed = {"column": "x", "method": "point_biserial", "value": round(r, 3)}
    # One pair tested: listed with max_p just above p
    assert _associations(df, max_p=p * 1.5)["target"] == [listed]
    # Sparse columns that don't qualify aren't tested, so the divisor stays 1
    for i in range(5):
        df[f"sparse{i}"] = np.nan
        df.loc[:3, f"sparse{i}"] = rng.normal(size=4)
    assert _associations(df, max_p=p * 1.5)["target"] == [listed]
    # Two more pairs that do qualify: the divisor is 3, and it no longer passes
    df["y"] = rng.normal(size=n)
    df["z"] = rng.normal(size=n)
    assert listed not in _associations(df, max_p=p * 1.5)["target"]


def test_a_constant_float_column_is_not_a_comparison():
    # Standard pre-PR review: 0.1's float mean has a rounding error, so the
    # column looked as if it varied and gave r = 0.707 with p ~ 0
    df = pd.DataFrame({"c": [0.1] * 300, "t": [1.0] * 300})
    df.loc[:99, "t"] = np.nan
    assert _associations(df)["t"] is None  # its only comparison was skipped
    df["x"] = np.random.default_rng(0).normal(size=300)
    assert _associations(df)["t"] == []


def test_corrected_cramers_v_is_zero_without_association():
    assert corrected_cramers_v(0.0, 100, (2, 5)) == 0.0
    assert corrected_cramers_v(100.0, 100, (2, 2)) == pytest.approx(1.0, abs=0.02)
