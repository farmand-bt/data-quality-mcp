"""The report contract, as TypedDicts.

This is the single source of truth for the report shape. It lives in the core
(pure typing, no `mcp` import) so every transport serves the same contract; the
MCP layer turns it into the tool's published output schema.

`typing_extensions.TypedDict` (not `typing.TypedDict`) is required: Pydantic,
which the MCP SDK uses to build schemas, rejects `typing.TypedDict` on
Python < 3.12. Sections typed `... | None` are null until the milestone that
computes them; null means "not computed", never "checked, none found".
"""

from typing import Any, Literal

from typing_extensions import TypedDict

InferredType = Literal["numeric", "categorical", "datetime", "boolean", "text", "mixed"]
QualityGrade = Literal["A", "B", "C", "D", "F"]
InputSource = Literal["file_path", "inline_content"]


class DatasetInfo(TypedDict):
    """Dataset-level summary and the composite quality score."""

    name: str
    source: InputSource
    rows: int
    columns: int
    quality_score: int
    quality_grade: QualityGrade


class OutlierInfo(TypedDict):
    """Per-column outlier detection results (computed from Milestone 2)."""

    iqr_count: int
    zscore_count: int
    iqr_indices: list[int]
    zscore_indices: list[int]


class ColumnReport(TypedDict):
    """Profile of a single column."""

    pandas_dtype: str
    inferred_type: InferredType
    type_mismatch: bool
    missing_count: int
    missing_pct: float
    stats: dict[str, Any]
    outliers: OutlierInfo | None
    warnings: list[str]


class DuplicateInfo(TypedDict):
    """Exact duplicate-row detection results (computed from Milestone 2)."""

    exact_count: int
    exact_pct: float
    sample_indices: list[int]


class CorrelationInfo(TypedDict):
    """Correlation analysis results (computed from Milestone 2)."""

    pearson: dict[str, dict[str, float | None]]
    high_correlation_pairs: list[dict[str, Any]]


class DatasetReport(TypedDict):
    """Full data quality report for one dataset."""

    dataset: DatasetInfo
    columns: dict[str, ColumnReport]
    duplicates: DuplicateInfo | None
    correlations: CorrelationInfo | None
    recommendations: list[dict[str, Any]]
    generated_at: str
