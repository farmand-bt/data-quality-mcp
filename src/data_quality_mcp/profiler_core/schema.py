"""The report contract, as TypedDicts.

This is the single source of truth for the report shape. It lives in the core
(pure typing, no `mcp` import) so every transport serves the same contract; the
MCP layer turns it into the tools' published output schemas.

`typing_extensions.TypedDict` (not `typing.TypedDict`) is required: Pydantic,
which the MCP SDK uses to build schemas, rejects `typing.TypedDict` on
Python < 3.12.

Row indices everywhere are 0-based positions of data rows (header excluded):
index i is line i + 2 of a CSV file. Index lists are capped (see
`indices_truncated`); counts and percentages are always exact.
"""

from typing import Any, Literal

from typing_extensions import TypedDict

InferredType = Literal["numeric", "categorical", "datetime", "boolean", "text", "mixed"]
QualityGrade = Literal["A", "B", "C", "D", "F"]
InputSource = Literal["file_path", "inline_content"]
Severity = Literal["critical", "warning", "info"]
RecommendationCategory = Literal[
    "missing", "type", "consistency", "duplicate", "outlier", "correlation"
]
CorrelationMethod = Literal["pearson", "cramers_v"]
MissingAssociationMethod = Literal["point_biserial", "cramers_v", "missing_together"]


class DatasetSummary(TypedDict):
    """Basic facts about the dataset."""

    name: str
    source: InputSource
    rows: int
    columns: int


class DatasetInfo(TypedDict):
    """Dataset-level summary and the composite quality score."""

    name: str
    source: InputSource
    rows: int
    columns: int
    quality_score: int
    quality_grade: QualityGrade


class SentinelValue(TypedDict):
    """An outlier value that may be a placeholder code (e.g. -1000, 9999999)."""

    value: float
    count: int  # input rows holding the value, duplicates included


class OutlierInfo(TypedDict):
    """Outliers in one numeric column, by IQR fences and by classic z-score."""

    iqr_count: int
    iqr_pct: float
    iqr_lower_bound: float | None
    iqr_upper_bound: float | None
    iqr_indices: list[int]
    zscore_count: int
    zscore_pct: float
    zscore_indices: list[int]
    indices_truncated: bool
    # Suspects to confirm, not a verdict; [] = checked, none (also without fences)
    possible_sentinels: list[SentinelValue]


class MissingAssociation(TypedDict):
    """An observed column whose values go with another column's missingness."""

    column: str
    method: MissingAssociationMethod
    value: float  # signed for point_biserial; > 0 for cramers_v, missing_together


class ColumnReport(TypedDict):
    """Profile of a single column."""

    pandas_dtype: str
    inferred_type: InferredType
    type_mismatch: bool
    missing_count: int
    missing_pct: float
    # None: not tested (no missing values, past the column cap, or no pair had
    # enough rows); []: at least one pair tested, none listed
    missing_related_to: list[MissingAssociation] | None
    stats: dict[str, Any]
    outliers: OutlierInfo | None  # None: not applicable (non-numeric / < 4 values)
    warnings: list[str]


class KeyColumnInfo(TypedDict):
    """An identifier-like column whose values repeat on rows with different data."""

    column: str
    conflicting_values: int  # key values on more than one non-identical row
    conflicting_rows: int
    sample_indices: list[int]
    indices_truncated: bool


class DuplicateInfo(TypedDict):
    """Exact and near-duplicate rows, and repeated values in key columns."""

    exact_count: int
    exact_pct: float
    sample_indices: list[int]
    near_duplicate_count: int
    near_duplicate_pct: float
    near_sample_indices: list[int]
    indices_truncated: bool
    key_columns: list[KeyColumnInfo]  # [] = no identifier-like column found


class HighCorrelationPair(TypedDict):
    """Two columns whose correlation magnitude reaches the threshold."""

    col_a: str
    col_b: str
    method: CorrelationMethod
    value: float


class CorrelationInfo(TypedDict):
    """Pearson (numeric) and Cramér's V (categorical) associations."""

    pearson: dict[str, dict[str, float | None]]
    cramers_v: dict[str, dict[str, float | None]]  # None: too few shared rows
    high_correlation_pairs: list[HighCorrelationPair]


class Recommendation(TypedDict):
    """An actionable finding; `column` is None for dataset-level findings."""

    severity: Severity
    category: RecommendationCategory
    column: str | None
    message: str


class DatasetReport(TypedDict):
    """Full data quality report for one dataset."""

    dataset: DatasetInfo
    columns: dict[str, ColumnReport]
    duplicates: DuplicateInfo
    correlations: CorrelationInfo
    recommendations: list[Recommendation]
    generated_at: str


# --- Results of the focused tools: basic dataset facts plus one section ---


class OutlierResult(TypedDict):
    """Outliers for every column (None where detection doesn't apply)."""

    dataset: DatasetSummary
    outliers: dict[str, OutlierInfo | None]


class DuplicateResult(TypedDict):
    """Duplicate-row analysis."""

    dataset: DatasetSummary
    duplicates: DuplicateInfo


class CorrelationResult(TypedDict):
    """Correlation analysis."""

    dataset: DatasetSummary
    correlations: CorrelationInfo


# --- Result of the cleaning tool (inputs are in cleaner_core/schema.py) ---

OutputFormat = Literal["csv", "xlsx"]


class CleaningLogEntry(TypedDict):
    """One applied step. Fields that don't apply to its action are None."""

    action: str
    column: str | None  # None for dataset-level steps (remove_duplicates)
    strategy: str
    rows_before: int
    rows_after: int
    rows_removed: int
    values_nulled: int | None  # non-null cells in `column` that became null
    original_dtype: str | None  # raw pandas dtypes (fix_type only)
    new_dtype: str | None
    missing_before: int | None  # handle_missing only
    missing_after: int | None
    outliers_treated: int | None  # handle_outliers only


class CleaningOutput(TypedDict):
    """Where the cleaned data went: a new file, or inline content."""

    path: str | None  # the new file, for file input; None for inline input
    content: str | None  # cleaned data for inline input (CSV text / XLSX base64)
    format: OutputFormat
    rows: int
    columns: int


class QualitySnapshot(TypedDict):
    """Headline quality figures of a dataset, as profile_dataset reports them."""

    rows: int
    columns: int
    missing_cells: int
    exact_duplicates: int
    quality_score: int
    quality_grade: QualityGrade


class CleaningResult(TypedDict):
    """Outcome of clean_dataset: output, before/after quality and the log."""

    dataset: DatasetSummary  # the input
    output: CleaningOutput
    before: QualitySnapshot
    after: QualitySnapshot  # profiled from the written output, loaded back
    log: list[CleaningLogEntry]  # in execution order
