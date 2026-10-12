import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data_quality_mcp.profiler_core.exceptions import InvalidInputError
from data_quality_mcp.profiler_core.json_safe import to_json_safe
from data_quality_mcp.profiler_core.loader import load_data
from data_quality_mcp.profiler_core.missing import analyze_missing
from data_quality_mcp.profiler_core.report import (
    compute_quality_score,
    generate_report,
    quality_grade,
)
from data_quality_mcp.profiler_core.schema import (
    ColumnReport,
    DatasetInfo,
    DatasetReport,
)
from data_quality_mcp.profiler_core.stats import compute_column_stats
from data_quality_mcp.profiler_core.type_detector import detect_types, infer_type


@pytest.fixture
def messy_report(messy_path: Path) -> dict:
    return generate_report(
        load_data(file_path=str(messy_path)), "messy.csv", "file_path"
    )


@pytest.fixture
def clean_report(clean_path: Path) -> dict:
    return generate_report(
        load_data(file_path=str(clean_path)), "clean.csv", "file_path"
    )


# --- type detection ---


def test_messy_inferred_types(messy_df: pd.DataFrame):
    types = detect_types(messy_df)
    expected = {
        "id": ("numeric", False),
        "price": ("numeric", False),
        "sqft": ("mixed", True),
        "neighborhood": ("categorical", False),
        "sale_date": ("datetime", True),
        "garage": ("boolean", True),
        "school_rating": ("numeric", False),
    }
    for col, (inferred, mismatch) in expected.items():
        assert types[col]["inferred_type"] == inferred, col
        assert types[col]["type_mismatch"] is mismatch, col


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1", "2", "3"], ("numeric", True)),
        ([True, False, True], ("boolean", False)),
        ([True, None, False], ("boolean", False)),  # pandas: bools + NaN => object
        (["yes", "no", "yes"], ("boolean", True)),
        (["yes", "yes"], ("categorical", False)),  # one distinct value isn't boolean
        ([None, None], ("categorical", False)),
        (["a", "b", "c", "d"], ("text", False)),
        (["a", "b", "a", "b"], ("categorical", False)),
    ],
)
def test_infer_type_cases(values, expected):
    assert infer_type(pd.Series(values, dtype=object)) == expected


def test_native_datetime_dtype():
    series = pd.Series(pd.to_datetime(["2020-01-01", "2021-06-01"]))
    assert infer_type(series) == ("datetime", False)


def test_high_cardinality_threshold_is_a_parameter():
    series = pd.Series(["a", "b", "c", "a"], dtype=object)  # 75% unique
    assert infer_type(series)[0] == "text"
    assert infer_type(series, high_cardinality_threshold=0.8)[0] == "categorical"


# --- stats ---


def test_mixed_stats_expose_non_numeric_values(messy_df: pd.DataFrame):
    stats = compute_column_stats(messy_df["sqft"], "mixed")
    assert stats["non_numeric_count"] > 0
    assert set(stats["non_numeric_examples"]) <= {"unknown", "TBD"}
    assert stats["mean"] is not None


def test_datetime_stats(messy_df: pd.DataFrame):
    stats = compute_column_stats(messy_df["sale_date"], "datetime")
    assert stats["min_date"].startswith("2020-01-01")
    assert stats["range_days"] == 364
    assert stats["unparseable_count"] == 0


def test_categorical_stats(messy_df: pd.DataFrame):
    stats = compute_column_stats(messy_df["neighborhood"], "categorical")
    assert stats["unique_count"] == 4
    assert sum(stats["top_values"].values()) == len(messy_df)


def test_text_stats():
    stats = compute_column_stats(pd.Series(["ab", "abcd"]), "text")
    assert stats == {
        "unique_count": 2,
        "avg_length": 3.0,
        "min_length": 2,
        "max_length": 4,
    }


def test_single_value_numeric_stats_have_no_nan():
    stats = compute_column_stats(pd.Series([5.0]), "numeric")
    assert stats["std"] is None  # undefined for one value: None, not NaN
    assert stats["mean"] == 5.0


def test_all_missing_numeric_stats():
    stats = compute_column_stats(pd.Series([np.nan, np.nan]), "numeric")
    assert stats["count"] == 0
    assert stats["mean"] is None


# --- missing values ---


def test_missing_analysis(messy_df: pd.DataFrame):
    info = analyze_missing(messy_df)
    assert info["per_column"]["id"] == {"missing_count": 0, "missing_pct": 0.0}
    rating = info["per_column"]["school_rating"]
    assert rating["missing_count"] == int(messy_df["school_rating"].isna().sum())
    assert rating["missing_pct"] > 30
    assert info["total_missing_cells"] == int(messy_df.isna().sum().sum())


# --- quality score ---


def test_score_perfect():
    assert compute_quality_score(overall_missing_pct=0, type_mismatch_ratio=0) == 100


def test_score_missing_penalty_is_capped():
    assert compute_quality_score(overall_missing_pct=90, type_mismatch_ratio=0) == 60


def test_score_type_mismatch_penalty():
    assert compute_quality_score(overall_missing_pct=0, type_mismatch_ratio=0.5) == 90


def test_score_uncomputed_components_are_skipped():
    base = compute_quality_score(overall_missing_pct=5, type_mismatch_ratio=0)
    with_dups = compute_quality_score(
        overall_missing_pct=5, type_mismatch_ratio=0, duplicate_pct=4, outlier_pct=1
    )
    assert base == 90
    assert with_dups == 90 - 4 - 2


def test_score_never_below_zero():
    score = compute_quality_score(
        overall_missing_pct=100,
        type_mismatch_ratio=1,
        duplicate_pct=100,
        outlier_pct=100,
    )
    assert score == 20  # every penalty is capped: 100 - 40 - 20 - 10 - 10


@pytest.mark.parametrize(
    ("score", "grade"),
    [(100, "A"), (90, "A"), (89, "B"), (70, "C"), (60, "D"), (59, "F")],
)
def test_quality_grade(score, grade):
    assert quality_grade(score) == grade


# --- report ---


def _assert_typed_dict_keys(obj: dict, typed_dict: type) -> None:
    assert set(obj) == set(typed_dict.__annotations__), typed_dict.__name__


def test_report_matches_schema(messy_report: dict):
    _assert_typed_dict_keys(messy_report, DatasetReport)
    _assert_typed_dict_keys(messy_report["dataset"], DatasetInfo)
    for column in messy_report["columns"].values():
        _assert_typed_dict_keys(column, ColumnReport)


def test_report_dataset_section(messy_report: dict):
    dataset = messy_report["dataset"]
    assert dataset["name"] == "messy.csv"
    assert dataset["source"] == "file_path"
    assert (dataset["rows"], dataset["columns"]) == (520, 10)
    assert 0 <= dataset["quality_score"] <= 100
    assert dataset["quality_grade"] == quality_grade(dataset["quality_score"])


def test_uncomputed_sections_are_null_not_zero(messy_report: dict):
    assert messy_report["duplicates"] is None
    assert messy_report["correlations"] is None
    assert messy_report["recommendations"] == []
    assert all(c["outliers"] is None for c in messy_report["columns"].values())


def test_report_is_strict_json(messy_report: dict):
    json.dumps(messy_report, allow_nan=False)


def test_generated_at_is_utc_iso(messy_report: dict):
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", messy_report["generated_at"]
    )


def test_clean_scores_higher_than_messy(clean_report: dict, messy_report: dict):
    assert clean_report["dataset"]["quality_score"] == 100
    assert (
        clean_report["dataset"]["quality_score"]
        > messy_report["dataset"]["quality_score"]
    )


def test_column_warnings(messy_report: dict):
    columns = messy_report["columns"]
    assert any("Type mismatch" in w for w in columns["sqft"]["warnings"])
    assert any("missing" in w for w in columns["school_rating"]["warnings"])
    assert columns["id"]["warnings"] == []


def test_warning_for_inconsistent_casing_and_constant_and_empty():
    df = pd.DataFrame(
        {
            "sex": ["Male", "male", "Female", "female"] * 5,
            "const": ["x"] * 20,
            "empty": [None] * 20,
        }
    )
    columns = generate_report(df, "d.csv", "inline_content")["columns"]
    assert any("Inconsistent labels" in w for w in columns["sex"]["warnings"])
    assert any("Constant column" in w for w in columns["const"]["warnings"])
    assert columns["empty"]["warnings"] == ["Column is entirely empty (100% missing)."]


def test_report_does_not_mutate_input(messy_df: pd.DataFrame):
    before = messy_df.copy()
    generate_report(messy_df, "m.csv", "file_path")
    pd.testing.assert_frame_equal(messy_df, before)


def test_non_string_and_colliding_column_names():
    df = pd.DataFrame([[1, 2, 3]], columns=[1, "1", "b"])
    report = generate_report(df, "d.csv", "file_path")
    assert list(report["columns"]) == ["1", "1.1", "b"]


def test_invalid_source_rejected(messy_df: pd.DataFrame):
    with pytest.raises(InvalidInputError, match="source"):
        generate_report(messy_df, "m.csv", "url")


# --- JSON safety helper ---


def test_to_json_safe_converts_numpy_and_pandas_values():
    converted = to_json_safe(
        {
            1: np.int64(3),
            "f": np.float32(1.5),
            "nan": float("nan"),
            "inf": np.inf,
            "b": np.bool_(True),
            "ts": pd.Timestamp("2020-01-02"),
            "nat": pd.NaT,
            "dt64nat": np.datetime64("NaT", "ns"),
            "na": pd.NA,
            "arr": np.array([1, 2]),
            "tuple": (1, None),
            "other": Path("x"),
        }
    )
    assert converted == {
        "1": 3,
        "f": 1.5,
        "nan": None,
        "inf": None,
        "b": True,
        "ts": "2020-01-02T00:00:00",
        "nat": None,
        "dt64nat": None,
        "na": None,
        "arr": [1, 2],
        "tuple": [1, None],
        "other": "x",
    }
    json.dumps(converted, allow_nan=False)
