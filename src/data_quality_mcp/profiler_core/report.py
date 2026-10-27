"""Aggregate the profiling modules into the unified report (see schema.py).

`generate_report` builds the full report. `outlier_report`, `duplicate_report`
and `correlation_report` build one section each, for the focused tools. All of
them return JSON-safe dicts and never modify the input DataFrame.
"""

from collections.abc import Mapping
from datetime import datetime, timezone

import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.correlations import compute_correlations
from data_quality_mcp.profiler_core.duplicates import analyze_duplicates
from data_quality_mcp.profiler_core.exceptions import InvalidInputError
from data_quality_mcp.profiler_core.json_safe import to_json_safe
from data_quality_mcp.profiler_core.missing import (
    analyze_missing,
    missing_associations,
)
from data_quality_mcp.profiler_core.outliers import detect_outliers, outlier_score_pct
from data_quality_mcp.profiler_core.recommendations import generate_recommendations
from data_quality_mcp.profiler_core.schema import (
    ColumnReport,
    CorrelationResult,
    DatasetReport,
    DatasetSummary,
    DuplicateResult,
    OutlierInfo,
    OutlierResult,
    QualitySnapshot,
)
from data_quality_mcp.profiler_core.scoring import compute_quality_score, quality_grade
from data_quality_mcp.profiler_core.stats import compute_column_stats
from data_quality_mcp.profiler_core.type_detector import (
    detect_types,
    has_inconsistent_casing,
)

_SOURCES = ("file_path", "inline_content")

_TYPE_MISMATCH_HINTS = {
    "numeric": "values are numbers stored as text; convert the column to a "
    "numeric type",
    "mixed": "values are mostly numeric but include non-numeric entries (see "
    "stats.non_numeric_examples); clean those before converting to numeric",
    "datetime": "values are dates stored as text, possibly in several formats; "
    "parse them as datetimes",
    "boolean": "values are boolean-like strings (e.g. 'Yes'/'no'/'Y'/'TRUE'); "
    "normalize them to true/false",
}


def _column_warnings(
    series: pd.Series,
    type_info: dict,
    missing_pct: float,
    stats: dict,
    outliers: OutlierInfo | None,
    *,
    inconsistent_casing: bool,
    missing_threshold: float,
    outlier_warning_pct: float,
) -> list[str]:
    warnings: list[str] = []
    inferred = type_info["inferred_type"]

    if missing_pct == 100:
        warnings.append("Column is entirely empty (100% missing).")
    elif missing_pct > missing_threshold * 100:
        warnings.append(
            f"{missing_pct:.1f}% of values are missing (above the "
            f"{missing_threshold * 100:.0f}% threshold)."
        )

    if type_info["type_mismatch"]:
        hint = _TYPE_MISMATCH_HINTS.get(inferred, "check the column's type")
        warnings.append(
            f"Type mismatch (stored as '{type_info['pandas_dtype']}'): {hint}."
        )

    if inconsistent_casing:
        warnings.append(
            "Inconsistent labels: some categories differ only by letter case or "
            "surrounding whitespace (e.g. 'Male' vs 'male'); normalize them."
        )

    if inferred == "text":
        warnings.append(
            "High cardinality: most values are unique, so this is likely free "
            "text or an identifier rather than a category."
        )

    if missing_pct < 100 and series.nunique(dropna=True) == 1:
        warnings.append("Constant column: every non-missing value is identical.")

    infinite = stats.get("infinite_count", 0)
    if infinite:
        warnings.append(
            f"{infinite:,} infinite value(s) (inf/-inf): replace them with a real "
            "value or treat them as missing; statistics here exclude them."
        )

    if outliers and outliers["iqr_pct"] > outlier_warning_pct:
        warnings.append(
            f"{outliers['iqr_count']:,} outliers detected "
            f"({outliers['iqr_pct']:.1f}%, IQR method)."
        )

    return warnings


def _unique_column_names(columns: pd.Index) -> list[str]:
    """Stringify column names, de-duplicating any collisions (e.g. 1 and "1")."""
    seen: dict[str, int] = {}
    names = []
    for col in columns:
        name = str(col)
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        seen.setdefault(name, 0)
        names.append(name)
    return names


def prepare_frame(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Validate `source`; return a copy with unique string column names and a
    0-based RangeIndex, so every reported index is a data-row position.

    The cleaner uses this too, so step columns match the report's names."""
    if source not in _SOURCES:
        raise InvalidInputError(f"source must be one of {_SOURCES}, got {source!r}.")
    return df.set_axis(_unique_column_names(df.columns), axis=1).reset_index(drop=True)


def quality_snapshot(report: DatasetReport) -> QualitySnapshot:
    """The headline figures of a report (clean_dataset's before/after)."""
    dataset = report["dataset"]
    return {
        "rows": dataset["rows"],
        "columns": dataset["columns"],
        "missing_cells": sum(c["missing_count"] for c in report["columns"].values()),
        "exact_duplicates": report["duplicates"]["exact_count"],
        "quality_score": dataset["quality_score"],
        "quality_grade": dataset["quality_grade"],
    }


def _iso_dates_typed(input_format: str | None) -> bool:
    """CSV has no date type, so ISO date text isn't a type mismatch there
    (planning/milestone-3.5.md, D4). Without a known format, keep the M1 rule."""
    return (input_format or "").lower().lstrip(".") == "csv"


def _summary(df: pd.DataFrame, name: str, source: str) -> DatasetSummary:
    return {"name": name, "source": source, "rows": len(df), "columns": len(df.columns)}


def generate_report(
    df: pd.DataFrame,
    name: str,
    source: str,
    *,
    missing_threshold: float = config.MISSING_THRESHOLD,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
    grade_thresholds: Mapping[str, int] = config.QUALITY_GRADE_THRESHOLDS,
    outlier_warning_pct: float = config.OUTLIER_COLUMN_WARNING_PCT,
    input_format: str | None = None,
) -> DatasetReport:
    """Profile `df` and return a JSON-safe report matching `DatasetReport`.

    `input_format` is the file format the data came from (".csv" or ".xlsx");
    for CSV, ISO date text counts as typed.
    """
    df = prepare_frame(df, source)
    type_info = detect_types(
        df,
        high_cardinality_threshold=high_cardinality_threshold,
        iso_dates_typed=_iso_dates_typed(input_format),
    )
    missing_info = analyze_missing(df)
    associations = missing_associations(df, type_info)
    outliers = detect_outliers(df, type_info)
    duplicates = analyze_duplicates(df)
    correlations = compute_correlations(df, type_info)
    inconsistent = {
        col
        for col in df.columns
        if type_info[col]["inferred_type"] == "categorical"
        and has_inconsistent_casing(df[col])
    }

    columns: dict[str, ColumnReport] = {}
    for col in df.columns:
        col_missing = missing_info["per_column"][col]
        stats = compute_column_stats(df[col], type_info[col]["inferred_type"])
        columns[col] = {
            "pandas_dtype": type_info[col]["pandas_dtype"],
            "inferred_type": type_info[col]["inferred_type"],
            "type_mismatch": type_info[col]["type_mismatch"],
            "missing_count": col_missing["missing_count"],
            "missing_pct": col_missing["missing_pct"],
            "missing_related_to": associations[col],
            "stats": stats,
            "outliers": outliers[col],
            "warnings": _column_warnings(
                df[col],
                type_info[col],
                col_missing["missing_pct"],
                stats,
                outliers[col],
                inconsistent_casing=col in inconsistent,
                missing_threshold=missing_threshold,
                outlier_warning_pct=outlier_warning_pct,
            ),
        }

    n_columns = len(df.columns)
    mismatched = sum(1 for info in type_info.values() if info["type_mismatch"])
    score = compute_quality_score(
        overall_missing_pct=missing_info["overall_missing_pct"],
        type_mismatch_ratio=mismatched / n_columns if n_columns else 0.0,
        duplicate_pct=duplicates["exact_pct"],
        outlier_pct=outlier_score_pct(outliers),
    )

    report = {
        "dataset": {
            **_summary(df, name, source),
            "quality_score": score,
            "quality_grade": quality_grade(score, grade_thresholds),
        },
        "columns": columns,
        "duplicates": duplicates,
        "correlations": correlations,
        "recommendations": [],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    report["recommendations"] = generate_recommendations(
        report, inconsistent_columns=inconsistent, missing_threshold=missing_threshold
    )
    return to_json_safe(report)


def outlier_report(
    df: pd.DataFrame,
    name: str,
    source: str,
    *,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
    input_format: str | None = None,
) -> OutlierResult:
    """Dataset summary plus per-column outliers (None where not applicable)."""
    df = prepare_frame(df, source)
    type_info = detect_types(
        df,
        high_cardinality_threshold=high_cardinality_threshold,
        iso_dates_typed=_iso_dates_typed(input_format),
    )
    return to_json_safe(
        {
            "dataset": _summary(df, name, source),
            "outliers": detect_outliers(df, type_info),
        }
    )


def duplicate_report(
    df: pd.DataFrame, name: str, source: str, *, input_format: str | None = None
) -> DuplicateResult:
    """Dataset summary plus exact and near-duplicate rows and key-column
    conflicts. (`input_format` is accepted for a uniform call; types aren't
    used here.)"""
    df = prepare_frame(df, source)
    return to_json_safe(
        {"dataset": _summary(df, name, source), "duplicates": analyze_duplicates(df)}
    )


def correlation_report(
    df: pd.DataFrame,
    name: str,
    source: str,
    *,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
    input_format: str | None = None,
) -> CorrelationResult:
    """Dataset summary plus Pearson / Cramér's V correlations."""
    df = prepare_frame(df, source)
    type_info = detect_types(
        df,
        high_cardinality_threshold=high_cardinality_threshold,
        iso_dates_typed=_iso_dates_typed(input_format),
    )
    return to_json_safe(
        {
            "dataset": _summary(df, name, source),
            "correlations": compute_correlations(df, type_info),
        }
    )
