"""MCP-facing profiling tools: thin adapters over profiler_core.

Each tool loads the file, calls one core function, and returns its result.
Domain errors from the core are turned into ToolError, whose message reaches
the calling agent; any other exception is reported generically by the SDK.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

import pandas as pd
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from data_quality_mcp.profiler_core import DataQualityError, load_data
from data_quality_mcp.profiler_core.report import (
    correlation_report,
    duplicate_report,
    generate_report,
    outlier_report,
)
from data_quality_mcp.profiler_core.schema import (
    CorrelationResult,
    DatasetReport,
    DuplicateResult,
    OutlierResult,
)

T = TypeVar("T")

FilePath = Annotated[
    str,
    Field(
        description=(
            "Absolute path to a .csv or .xlsx file on the machine running this "
            "server, e.g. 'C:\\data\\sales.csv' or '/home/me/data/sales.csv'."
        )
    ),
]

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _run(file_path: str, build: Callable[[pd.DataFrame, str, str], T]) -> T:
    """Load `file_path` and pass it to a core report builder, mapping domain
    errors to ToolError so the agent sees an actionable message."""
    try:
        df = load_data(file_path=file_path)
        return build(df, Path(file_path).name, "file_path")
    except DataQualityError as exc:
        raise ToolError(str(exc)) from exc


def profile_dataset(file_path: FilePath) -> DatasetReport:
    """Profile a tabular dataset and return a full, structured data quality report.

    Use this tool to assess a CSV or Excel file before analyzing, cleaning, or
    modeling it. It covers missing values, type mismatches (e.g. numbers stored
    as text, dates in several formats), per-column statistics, outliers,
    duplicate rows, correlations, prioritized recommendations, and an overall
    quality score. It only reads the file. The analysis is deterministic. For a
    narrow question, the focused tools (detect_outliers, find_duplicates,
    analyze_correlations) return a smaller result.

    Input: `file_path` must be an absolute path. Relative paths resolve against
    the server's working directory, which is usually not what you expect.
    Supported formats: .csv (comma- or semicolon-separated) and .xlsx (first
    sheet). Limits: 50 MB and 500,000 rows.

    Output sections:
    - dataset: name, rows, columns, quality_score (0-100, higher is better) and
      quality_grade (A >= 90, B >= 80, C >= 70, D >= 60, otherwise F). Read the
      resource dataquality://docs/quality-score for the exact formula.
    - recommendations: actionable findings sorted critical -> warning -> info,
      each with a category (missing, type, consistency, duplicate, outlier,
      correlation) and a column (null = dataset-level). Start here.
    - columns: one entry per column with pandas_dtype (how it is stored),
      inferred_type (numeric, categorical, datetime, boolean, text or mixed),
      type_mismatch, missing_count, missing_pct, stats (fields depend on
      inferred_type; for numeric/mixed columns, non_numeric_examples shows the
      offending values), outliers (see detect_outliers; null when not
      applicable), and warnings (plain-language issues with suggested fixes).
    - duplicates: see find_duplicates. correlations: see analyze_correlations.
    - generated_at: UTC timestamp.

    Row indices anywhere in the report are 0-based data-row positions (header
    excluded): index i is line i + 2 of a CSV file. Index lists are capped at 50
    entries (indices_truncated says when); counts and percentages are exact.

    Errors: the message says what to fix, e.g. file not found (check the path is
    absolute), unsupported file type, file too large, or unparseable content.
    """
    return _run(file_path, generate_report)


def detect_outliers(file_path: FilePath) -> OutlierResult:
    """Find outliers in every numeric column of a CSV or Excel file.

    Use this when you only need outliers; profile_dataset includes the same
    data plus everything else. Read-only and deterministic.

    Methods, per numeric (or mostly numeric) column:
    - IQR (primary): values outside [Q1 - 1.5 x IQR, Q3 + 1.5 x IQR].
      iqr_lower_bound / iqr_upper_bound are those fences (null when IQR is 0,
      i.e. a constant column, in which case nothing is flagged).
    - Classic z-score (secondary): |x - mean| / std > 3. Caveat: extreme values
      inflate the std and can hide other outliers, so the z-score often flags
      fewer values than IQR. Prefer IQR when they disagree.

    Output: dataset (name, source, rows, columns) and outliers, a mapping from
    every column to its result, or null where detection doesn't apply
    (non-numeric columns, or fewer than 4 numeric values). *_pct is relative to
    all rows. *_indices are 0-based data-row positions (CSV line = index + 2),
    sorted and capped at 50; indices_truncated is true when a list was cut.

    Input: an absolute path to a .csv or .xlsx file (50 MB / 500,000 rows max).
    Errors explain what to fix (e.g. use an absolute path).
    """
    return _run(file_path, outlier_report)


def find_duplicates(file_path: FilePath) -> DuplicateResult:
    """Find exact and near-duplicate rows in a CSV or Excel file.

    Use this when you only need duplicate analysis; profile_dataset includes the
    same data plus everything else. Read-only and deterministic.

    Output: dataset (name, source, rows, columns) and duplicates:
    - exact_count / exact_pct: rows that would be removed by deduplication
      (keeping the first copy of each group).
    - sample_indices: rows belonging to any exact-duplicate group.
    - near_duplicate_count / near_duplicate_pct / near_sample_indices: rows
      identical to another row on every column except exactly one, excluding
      exact duplicates. Often legitimate (e.g. a corrected value), so review
      before removing. Not computed for 1 column or more than 50 columns.
    Indices are 0-based data-row positions (CSV line = index + 2), sorted and
    capped at 50; indices_truncated is true when a list was cut.

    Input: an absolute path to a .csv or .xlsx file (50 MB / 500,000 rows max).
    Errors explain what to fix (e.g. use an absolute path).
    """
    return _run(file_path, duplicate_report)


def analyze_correlations(file_path: FilePath) -> CorrelationResult:
    """Measure correlations between columns of a CSV or Excel file.

    Use this to find redundant or strongly related features; profile_dataset
    includes the same data plus everything else. Read-only and deterministic.

    Output: dataset (name, source, rows, columns) and correlations:
    - pearson: full matrix over numeric/mixed columns (-1 to 1; null where
      undefined, e.g. a constant column). At most the first 50 numeric columns.
    - cramers_v: association between categorical/boolean columns (0 to 1),
      upper triangle only. At most the first 15 such columns with no more than
      50 distinct values each; others are skipped.
    - high_correlation_pairs: pairs with |value| >= 0.9, strongest first, each
      with col_a, col_b, method (pearson or cramers_v) and value.

    Input: an absolute path to a .csv or .xlsx file (50 MB / 500,000 rows max).
    Errors explain what to fix (e.g. use an absolute path).
    """
    return _run(file_path, correlation_report)


def register(app: MCPServer) -> None:
    """Register the profiling tools on `app`."""
    for fn, title in (
        (profile_dataset, "Profile dataset"),
        (detect_outliers, "Detect outliers"),
        (find_duplicates, "Find duplicate rows"),
        (analyze_correlations, "Analyze correlations"),
    ):
        app.add_tool(fn, title=title, annotations=_READ_ONLY)
