"""Rule-based recommendations derived from a full report.

Ported from the Data Quality Analyzer (`profiler/recommendations.py`) with the
same rules, severities and ordering. Deviation (see planning/milestone-2.md):
an added `consistency` category for inconsistent category labels, which this
server reports as a warning rather than as a type mismatch.
"""

from collections.abc import Collection

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import Recommendation

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_CRITICAL_MISSING_PCT = 50.0
_CRITICAL_DUPLICATE_PCT = 5.0
_CRITICAL_OUTLIER_PCT = 10.0
_WARNING_OUTLIER_PCT = 3.0


def generate_recommendations(
    report: dict,
    *,
    inconsistent_columns: Collection[str] = (),
    missing_threshold: float = config.MISSING_THRESHOLD,
) -> list[Recommendation]:
    """Return recommendations sorted critical -> warning -> info.

    `report` needs the `columns`, `duplicates` and `correlations` sections.
    `inconsistent_columns` names categorical columns whose labels differ only
    by case or whitespace.
    """
    recs: list[Recommendation] = []
    threshold_pct = missing_threshold * 100

    # --- Dataset level ---
    duplicates = report["duplicates"]
    if duplicates["exact_count"]:
        pct = duplicates["exact_pct"]
        recs.append(
            {
                "severity": "critical" if pct > _CRITICAL_DUPLICATE_PCT else "warning",
                "category": "duplicate",
                "column": None,
                "message": (
                    f"{duplicates['exact_count']:,} exact duplicate rows detected "
                    f"({pct:.1f}%): consider deduplication before analysis or modeling."
                ),
            }
        )

    for key in duplicates.get("key_columns", []):
        if key["conflicting_values"]:
            rows = ", ".join(str(i) for i in key["sample_indices"][:5])
            more = " ..." if key["conflicting_rows"] > 5 else ""
            recs.append(
                {
                    "severity": "warning",
                    "category": "duplicate",
                    "column": key["column"],
                    "message": (
                        f"'{key['column']}' looks like a row identifier, but "
                        f"{key['conflicting_values']:,} value(s) appear on rows "
                        f"with different data (rows {rows}{more}): decide which "
                        "record is correct before deduplicating."
                    ),
                }
            )

    for pair in report["correlations"]["high_correlation_pairs"]:
        label = "Pearson r" if pair["method"] == "pearson" else "Cramér's V"
        recs.append(
            {
                "severity": "warning",
                "category": "correlation",
                "column": None,
                "message": (
                    f"'{pair['col_a']}' and '{pair['col_b']}' are highly correlated "
                    f"({label} = {pair['value']:.3f}): potential feature redundancy."
                ),
            }
        )

    # --- Per column ---
    for col, info in report["columns"].items():
        missing_pct = info["missing_pct"]
        if missing_pct >= _CRITICAL_MISSING_PCT:
            recs.append(
                {
                    "severity": "critical",
                    "category": "missing",
                    "column": col,
                    "message": (
                        f"{missing_pct:.1f}% of values are missing: consider "
                        "dropping this column."
                    ),
                }
            )
        elif missing_pct >= threshold_pct:
            recs.append(
                {
                    "severity": "warning",
                    "category": "missing",
                    "column": col,
                    "message": (
                        f"{missing_pct:.1f}% of values are missing: consider "
                        "imputation or dropping."
                    ),
                }
            )

        for association in info.get("missing_related_to") or []:
            recs.append(
                {
                    "severity": "warning",
                    "category": "missing",
                    "column": col,
                    "message": _association_message(col, association),
                }
            )

        if info["type_mismatch"]:
            recs.append(
                {
                    "severity": "warning",
                    "category": "type",
                    "column": col,
                    "message": (
                        f"Stored as {info['pandas_dtype']} but inferred as "
                        f"{info['inferred_type']}: consider type conversion."
                    ),
                }
            )

        if col in inconsistent_columns:
            recs.append(
                {
                    "severity": "warning",
                    "category": "consistency",
                    "column": col,
                    "message": (
                        "Category labels differ only by case or whitespace (e.g. "
                        "'Male' vs 'male'): normalize them before grouping or encoding."
                    ),
                }
            )

        outliers = info["outliers"]
        if outliers and outliers["iqr_count"] > 0:
            pct = outliers["iqr_pct"]
            if pct > _CRITICAL_OUTLIER_PCT:
                severity = "critical"
            elif pct > _WARNING_OUTLIER_PCT:
                severity = "warning"
            else:
                severity = "info"
            recs.append(
                {
                    "severity": severity,
                    "category": "outlier",
                    "column": col,
                    "message": (
                        f"{outliers['iqr_count']:,} outliers detected ({pct:.1f}%, "
                        "IQR method): review before modeling, or impute/remove them."
                    ),
                }
            )
        if outliers and outliers.get("possible_sentinels"):
            listed = ", ".join(
                f"{_number(s['value'])} ({s['count']:,} rows)"
                for s in outliers["possible_sentinels"]
            )
            recs.append(
                {
                    "severity": "warning",
                    "category": "outlier",
                    "column": col,
                    "message": (
                        f"'{col}' has extreme values that may be placeholder codes "
                        f"rather than measurements: {listed}. Check with the data "
                        "owner; values confirmed as codes can be set to missing "
                        "with clean_dataset (handle_outliers, method 'sentinel', "
                        "values [...])."
                    ),
                }
            )

    recs.sort(key=lambda rec: _SEVERITY_ORDER[rec["severity"]])
    return recs


def _number(value: float) -> str:
    """-1000.0 -> "-1000", 2.5 -> "2.5"."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:g}"


_ASSOCIATION_TEMPLATES = {
    "point_biserial": (
        "'{col}' is missing more often where '{other}' is {direction} "
        "(point-biserial r = {value:.2f}, p < 0.001)."
    ),
    "cramers_v": (
        "'{col}' is missing more often for some values of '{other}' "
        "(Cramér's V = {value:.2f}, p < 0.001)."
    ),
    "missing_together": (
        "'{col}' tends to be missing in the same rows as '{other}' "
        "(phi = {value:.2f}, p < 0.001)."
    ),
}


def _association_message(col: str, association: dict) -> str:
    """An observation, not a verdict: the data can show that gaps go with
    another column, not why (planning/milestone-3.5.md, D1)."""
    other = association["column"]
    observed = _ASSOCIATION_TEMPLATES[association["method"]].format(
        col=col,
        other=other,
        direction="higher" if association["value"] > 0 else "lower",
        value=association["value"],
    )
    return (
        f"{observed} If the gaps depend on '{other}', a mean or median fill "
        "would bias the column."
    )
