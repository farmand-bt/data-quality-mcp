"""Duplicate detection. The TestAnalyzerParity cases are ported from the Data
Quality Analyzer's TestDuplicates."""

import pandas as pd
import pytest

from data_quality_mcp.profiler_core.duplicates import analyze_duplicates
from data_quality_mcp.profiler_core.schema import DuplicateInfo


class TestAnalyzerParity:
    def test_exact_duplicates_detected(self):
        df = pd.DataFrame({"a": [1, 2, 1], "b": ["x", "y", "x"]})
        assert analyze_duplicates(df)["exact_count"] == 1

    def test_no_duplicates(self):
        result = analyze_duplicates(
            pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        )
        assert result["exact_count"] == 0
        assert result["exact_pct"] == 0.0

    def test_exact_pct(self):
        df = pd.DataFrame({"a": [1, 1, 2, 3, 4], "b": [1, 1, 2, 3, 4]})
        result = analyze_duplicates(df)
        assert result["exact_count"] == 1
        assert result["exact_pct"] == pytest.approx(20.0)

    def test_sample_indices_valid(self):
        df = pd.DataFrame({"a": [1, 2, 1], "b": ["x", "y", "x"]})
        for idx in analyze_duplicates(df)["sample_indices"]:
            assert idx in df.index

    def test_empty_dataframe(self):
        result = analyze_duplicates(pd.DataFrame({"a": [], "b": []}))
        assert result["exact_count"] == 0
        assert result["near_duplicate_count"] == 0

    def test_schema_keys_present(self, messy_df: pd.DataFrame):
        assert set(analyze_duplicates(messy_df)) == set(DuplicateInfo.__annotations__)


def test_sample_indices_list_every_copy():
    df = pd.DataFrame({"a": [1, 2, 1, 1], "b": ["x", "y", "x", "x"]})
    result = analyze_duplicates(df)
    assert result["exact_count"] == 2  # rows dropped keeping the first copy
    assert result["sample_indices"] == [0, 2, 3]


def test_near_duplicates_differ_in_exactly_one_column():
    df = pd.DataFrame({"a": [1, 1, 3, 4], "b": [2, 2, 5, 6], "c": ["x", "z", "q", "w"]})
    result = analyze_duplicates(df)
    assert result["exact_count"] == 0
    assert result["near_duplicate_count"] == 2
    assert result["near_sample_indices"] == [0, 1]


def test_exact_duplicates_are_not_also_near_duplicates():
    df = pd.DataFrame({"a": [1, 1, 2], "b": [1, 1, 3]})
    result = analyze_duplicates(df)
    assert result["exact_count"] == 1
    assert result["near_duplicate_count"] == 0


def test_near_duplicates_skipped_above_column_limit():
    df = pd.DataFrame({"a": [1, 1], "b": [2, 2], "c": [3, 4]})
    result = analyze_duplicates(df, max_near_duplicate_columns=2)
    assert result["near_duplicate_count"] == 0


def test_single_column_has_no_near_duplicates():
    result = analyze_duplicates(pd.DataFrame({"a": [1, 2, 3]}))
    assert result["near_duplicate_count"] == 0


def test_indices_are_capped_and_flagged():
    df = pd.DataFrame({"a": [1] * 10, "b": [1] * 10})
    result = analyze_duplicates(df, max_indices=4)
    assert result["exact_count"] == 9
    assert result["sample_indices"] == [0, 1, 2, 3]
    assert result["indices_truncated"] is True
