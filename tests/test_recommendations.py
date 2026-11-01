"""Recommendations. The TestAnalyzerParity cases are ported from the Data
Quality Analyzer's TestRecommendations; the rule tests pin its thresholds."""

import pandas as pd
import pytest

from data_quality_mcp.profiler_core.recommendations import generate_recommendations
from data_quality_mcp.profiler_core.report import generate_report
from data_quality_mcp.profiler_core.schema import Recommendation


def _recs(df: pd.DataFrame) -> list[dict]:
    return generate_report(df, "t.csv", "file_path")["recommendations"]


class TestAnalyzerParity:
    def test_missing_recommendation(self):
        recs = _recs(pd.DataFrame({"x": [None] * 8 + [1.0, 2.0]}))
        assert any(r["category"] == "missing" and r["column"] == "x" for r in recs)

    def test_duplicate_recommendation(self):
        recs = _recs(pd.DataFrame({"x": [1, 1, 2, 3, 4], "y": [1, 1, 2, 3, 4]}))
        assert any(r["category"] == "duplicate" for r in recs)

    def test_outlier_recommendation(self):
        recs = _recs(pd.DataFrame({"x": list(range(1, 21)) + [10000.0]}))
        assert any(r["category"] == "outlier" and r["column"] == "x" for r in recs)

    def test_schema(self, messy_df: pd.DataFrame):
        for rec in _recs(messy_df):
            assert set(rec) == set(Recommendation.__annotations__)
            assert rec["severity"] in ("critical", "warning", "info")


def _report(**overrides) -> dict:
    """A minimal report skeleton for exercising individual rules."""
    report = {
        "columns": {},
        "duplicates": {"exact_count": 0, "exact_pct": 0.0},
        "correlations": {"high_correlation_pairs": []},
    }
    report.update(overrides)
    return report


def _column(**overrides) -> dict:
    column = {
        "pandas_dtype": "float64",
        "inferred_type": "numeric",
        "type_mismatch": False,
        "missing_pct": 0.0,
        "outliers": None,
    }
    column.update(overrides)
    return column


@pytest.mark.parametrize(
    ("exact_pct", "severity"), [(5.0, "warning"), (5.01, "critical")]
)
def test_duplicate_severity(exact_pct, severity):
    recs = generate_recommendations(
        _report(duplicates={"exact_count": 3, "exact_pct": exact_pct})
    )
    assert recs[0]["severity"] == severity


@pytest.mark.parametrize(
    ("missing_pct", "severity"), [(29.9, None), (30.0, "warning"), (50.0, "critical")]
)
def test_missing_severity(missing_pct, severity):
    recs = generate_recommendations(
        _report(columns={"x": _column(missing_pct=missing_pct)})
    )
    assert [r["severity"] for r in recs] == ([severity] if severity else [])


@pytest.mark.parametrize(
    ("iqr_pct", "severity"),
    [(3.0, "info"), (3.01, "warning"), (10.0, "warning"), (10.01, "critical")],
)
def test_outlier_severity(iqr_pct, severity):
    outliers = {"iqr_count": 5, "iqr_pct": iqr_pct}
    recs = generate_recommendations(_report(columns={"x": _column(outliers=outliers)}))
    assert recs[0]["severity"] == severity


def test_type_mismatch_and_correlation_are_warnings():
    pair = {"col_a": "a", "col_b": "b", "method": "cramers_v", "value": 0.95}
    recs = generate_recommendations(
        _report(
            columns={"x": _column(type_mismatch=True, inferred_type="mixed")},
            correlations={"high_correlation_pairs": [pair]},
        )
    )
    assert {(r["category"], r["severity"]) for r in recs} == {
        ("correlation", "warning"),
        ("type", "warning"),
    }
    assert any("Cramér's V = 0.950" in r["message"] for r in recs)


def test_consistency_recommendation():
    df = pd.DataFrame(
        {"id": range(20), "sex": ["Male", "male", "Female", "female"] * 5}
    )
    recs = _recs(df)
    assert [(r["category"], r["column"]) for r in recs] == [("consistency", "sex")]


def test_sorted_critical_first():
    recs = generate_recommendations(
        _report(
            duplicates={"exact_count": 1, "exact_pct": 1.0},  # warning
            columns={
                "a": _column(outliers={"iqr_count": 1, "iqr_pct": 1.0}),  # info
                "b": _column(missing_pct=80.0),  # critical
            },
        )
    )
    assert [r["severity"] for r in recs] == ["critical", "warning", "info"]


def test_sentinel_values_are_printed_exactly():
    # They are copied into clean_dataset's `values`, matched by float equality
    from data_quality_mcp.profiler_core.recommendations import _number

    assert _number(-1000.0) == "-1000"
    assert _number(9999999.0) == "9999999"
    assert _number(1234567.5) == "1234567.5"
    assert float(_number(0.1 + 0.2)) == 0.1 + 0.2
