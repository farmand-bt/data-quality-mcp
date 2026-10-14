"""The composite quality score and grade, plus a generated explanation of both.

The explanation is built from the same constants the formula uses, so the
documentation an agent reads can't drift from the code.
"""

from collections.abc import Mapping

from data_quality_mcp import config

# Penalty weights (points off 100) and caps. Same components as the analyzer.
MISSING_PENALTY_PER_PCT = 2.0
MISSING_PENALTY_CAP = 40.0
TYPE_MISMATCH_PENALTY = 20.0
DUPLICATE_PENALTY_PER_PCT = 1.0
DUPLICATE_PENALTY_CAP = 10.0
OUTLIER_PENALTY_PER_PCT = 2.0
OUTLIER_PENALTY_CAP = 10.0


def compute_quality_score(
    *,
    overall_missing_pct: float,
    type_mismatch_ratio: float,
    duplicate_pct: float | None = None,
    outlier_pct: float | None = None,
) -> int:
    """Composite 0-100 quality score: 100 minus weighted, capped penalties.

    `outlier_pct` is the average IQR outlier % across numeric columns that
    have any outliers (see outliers.outlier_score_pct). A component passed as
    None is skipped (no penalty).
    """
    score = 100.0
    score -= min(MISSING_PENALTY_CAP, overall_missing_pct * MISSING_PENALTY_PER_PCT)
    score -= type_mismatch_ratio * TYPE_MISMATCH_PENALTY
    if duplicate_pct is not None:
        score -= min(DUPLICATE_PENALTY_CAP, duplicate_pct * DUPLICATE_PENALTY_PER_PCT)
    if outlier_pct is not None:
        score -= min(OUTLIER_PENALTY_CAP, outlier_pct * OUTLIER_PENALTY_PER_PCT)
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


def quality_score_explanation(
    thresholds: Mapping[str, int] = config.QUALITY_GRADE_THRESHOLDS,
) -> str:
    """Markdown explanation of how quality_score and quality_grade are computed."""
    grades = ", ".join(
        f"{grade} >= {minimum}"
        for grade, minimum in sorted(thresholds.items(), key=lambda item: -item[1])
    )
    rows = [
        (
            "Completeness",
            f"{MISSING_PENALTY_PER_PCT:g} x overall missing-cell %",
            MISSING_PENALTY_CAP,
        ),
        (
            "Type consistency",
            f"{TYPE_MISMATCH_PENALTY:g} x (columns with type_mismatch) / (all columns)",
            TYPE_MISMATCH_PENALTY,
        ),
        (
            "Duplicates",
            f"{DUPLICATE_PENALTY_PER_PCT:g} x exact-duplicate-row %",
            DUPLICATE_PENALTY_CAP,
        ),
        (
            "Outliers",
            f"{OUTLIER_PENALTY_PER_PCT:g} x average IQR outlier % across numeric "
            "columns that have outliers",
            OUTLIER_PENALTY_CAP,
        ),
    ]
    table = "\n".join(f"| {name} | {rule} | {cap:g} |" for name, rule, cap in rows)
    iqr = config.OUTLIER_IQR_MULTIPLIER
    z = config.OUTLIER_ZSCORE_THRESHOLD
    return f"""# How the data quality score is computed

`quality_score` starts at 100 and loses a capped penalty per component. The
result is clamped to 0-100 and rounded. Higher is better.

| Component | Penalty | Cap |
|---|---|---|
{table}

`quality_grade`: {grades}, otherwise F.

Notes:
- `type_mismatch` means values are stored as a different type than they
  represent (numbers or dates stored as text, boolean-like strings).
  Inconsistent category casing is reported as a warning and a `consistency`
  recommendation, and does not affect the score.
- Outliers use the IQR method ({iqr:g} x IQR fences) for the score. The
  classic z-score (|x - mean| / std > {z:g}) is reported as well, but extreme
  values inflate the std and can hide other outliers, so treat it as secondary.
- Near-duplicates (rows identical on all but one column) are reported but not
  scored, because they are often legitimate.
"""
