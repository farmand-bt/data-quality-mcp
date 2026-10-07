"""Aggregate the profiling modules into the unified report (see schema.py)."""

from collections.abc import Mapping
from datetime import datetime, timezone

import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.exceptions import InvalidInputError
from data_quality_mcp.profiler_core.json_safe import to_json_safe
from data_quality_mcp.profiler_core.missing import analyze_missing
from data_quality_mcp.profiler_core.schema import ColumnReport, DatasetReport
from data_quality_mcp.profiler_core.stats import compute_column_stats
from data_quality_mcp.profiler_core.type_detector import (
    detect_types,
    has_inconsistent_casing,
)

_SOURCES = ("file_path", "inline_content")

# Penalty weights (points off 100) and caps. See docs/architecture.md.
_MISSING_PENALTY_PER_PCT = 2.0
_MISSING_PENALTY_CAP = 40.0
_TYPE_MISMATCH_PENALTY = 20.0
_DUPLICATE_PENALTY_PER_PCT = 1.0
_DUPLICATE_PENALTY_CAP = 10.0
_OUTLIER_PENALTY_PER_PCT = 2.0
_OUTLIER_PENALTY_CAP = 10.0

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


def compute_quality_score(
    *,
    overall_missing_pct: float,
    type_mismatch_ratio: float,
    duplicate_pct: float | None = None,
    outlier_pct: float | None = None,
) -> int:
    """Composite 0-100 quality score: 100 minus weighted, capped penalties.

    A component passed as None has not been computed yet and is skipped (no
    penalty). Milestone 1 computes completeness and type consistency only.
    """
    score = 100.0
    score -= min(_MISSING_PENALTY_CAP, overall_missing_pct * _MISSING_PENALTY_PER_PCT)
    score -= type_mismatch_ratio * _TYPE_MISMATCH_PENALTY
    if duplicate_pct is not None:
        score -= min(_DUPLICATE_PENALTY_CAP, duplicate_pct * _DUPLICATE_PENALTY_PER_PCT)
    if outlier_pct is not None:
        score -= min(_OUTLIER_PENALTY_CAP, outlier_pct * _OUTLIER_PENALTY_PER_PCT)
    return max(0, min(100, round(score)))


def quality_grade(
    score: int,
    thresholds: Mapping[str, int] = config.QUALITY_GRADE_THRESHOLDS,
) -> str:
    """Map a score to a letter grade; below the lowest threshold is "F"."""
    for grade, minimum in sorted(thresholds.items(), key=lambda item: -item[1]):
        if score >= minimum:
            return grade
    return "F"


def _column_warnings(
    series: pd.Series,
    type_info: dict,
    missing_pct: float,
    *,
    missing_threshold: float,
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

    if inferred == "categorical" and has_inconsistent_casing(series):
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


def generate_report(
    df: pd.DataFrame,
    name: str,
    source: str,
    *,
    missing_threshold: float = config.MISSING_THRESHOLD,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
    grade_thresholds: Mapping[str, int] = config.QUALITY_GRADE_THRESHOLDS,
) -> DatasetReport:
    """Profile `df` and return a JSON-safe report matching `DatasetReport`.

    The input DataFrame is never modified. Sections that later milestones
    compute (outliers, duplicates, correlations) are present and None.
    """
    if source not in _SOURCES:
        raise InvalidInputError(f"source must be one of {_SOURCES}, got {source!r}.")

    df = df.set_axis(_unique_column_names(df.columns), axis=1)  # returns a copy
    type_info = detect_types(df, high_cardinality_threshold=high_cardinality_threshold)
    missing_info = analyze_missing(df)

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
            "stats": stats,
            "outliers": None,  # Milestone 2
            "warnings": _column_warnings(
                df[col],
                type_info[col],
                col_missing["missing_pct"],
                missing_threshold=missing_threshold,
            ),
        }

    n_columns = len(df.columns)
    mismatched = sum(1 for info in type_info.values() if info["type_mismatch"])
    score = compute_quality_score(
        overall_missing_pct=missing_info["overall_missing_pct"],
        type_mismatch_ratio=mismatched / n_columns if n_columns else 0.0,
    )

    report = {
        "dataset": {
            "name": name,
            "source": source,
            "rows": len(df),
            "columns": n_columns,
            "quality_score": score,
            "quality_grade": quality_grade(score, grade_thresholds),
        },
        "columns": columns,
        "duplicates": None,  # Milestone 2
        "correlations": None,  # Milestone 2
        "recommendations": [],  # Milestone 2
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return to_json_safe(report)
