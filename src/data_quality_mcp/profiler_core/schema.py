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


class ColumnReport(TypedDict):
    """Profile of a single column."""

    pandas_dtype: str
    inferred_type: InferredType
    type_mismatch: bool
    missing_count: int
    missing_pct: float
    stats: dict[str, Any]
    outliers: OutlierInfo | None  # None: not applicable (non-numeric / < 4 values)
    warnings: list[str]


class DuplicateInfo(TypedDict):
    """Exact and near-duplicate rows."""

    exact_count: int
    exact_pct: float
    sample_indices: list[int]
    near_duplicate_count: int
    near_duplicate_pct: float
    near_sample_indices: list[int]
    indices_truncated: bool


class HighCorrelationPair(TypedDict):
    """Two columns whose correlation magnitude reaches the threshold."""

    col_a: str
    col_b: str
    method: CorrelationMethod
    value: float


class CorrelationInfo(TypedDict):
    """Pearson (numeric) and Cramér's V (categorical) associations."""

    pearson: dict[str, dict[str, float | None]]
    cramers_v: dict[str, dict[str, float]]
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
