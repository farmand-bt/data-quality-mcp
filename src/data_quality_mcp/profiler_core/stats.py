"""Per-column descriptive statistics; the fields depend on the inferred type.

Ported and simplified from the Data Quality Analyzer. Every value returned is a
native Python type, and non-finite floats become None.
"""

import math

import pandas as pd

from data_quality_mcp.profiler_core.type_detector import parse_datetimes

_TOP_VALUES = 5
_EXAMPLES = 5


def _finite(value: float, ndigits: int = 4) -> float | None:
    value = float(value)
    return round(value, ndigits) if math.isfinite(value) else None


def _numeric_stats(series: pd.Series) -> dict:
    parsed = pd.to_numeric(series, errors="coerce")
    values = parsed.dropna()
    unparseable = series[parsed.isna()].astype(str)
    stats: dict = {
        "count": len(values),
        "mean": None,
        "median": None,
        "std": None,
        "min": None,
        "max": None,
        "skewness": None,
        "kurtosis": None,
        "zeros": 0,
        "unique_count": 0,
        # Values that are not numbers: the reason a column is "mixed"
        "non_numeric_count": len(unparseable),
        "non_numeric_examples": unparseable.unique()[:_EXAMPLES].tolist(),
    }
    if len(values) == 0:
        return stats
    stats.update(
        mean=_finite(values.mean()),
        median=_finite(values.median()),
        std=_finite(values.std()),
        min=_finite(values.min()),
        max=_finite(values.max()),
        skewness=_finite(values.skew()),
        kurtosis=_finite(values.kurtosis()),
        zeros=int((values == 0).sum()),
        unique_count=int(values.nunique()),
    )
    return stats


def _categorical_stats(series: pd.Series) -> dict:
    if len(series) == 0:
        return {"unique_count": 0, "top_values": {}, "cardinality_ratio": 0.0}
    top = series.value_counts().head(_TOP_VALUES)
    return {
        "unique_count": int(series.nunique()),
        "top_values": {str(k): int(v) for k, v in top.items()},
        "cardinality_ratio": round(series.nunique() / len(series), 4),
    }


def _datetime_stats(series: pd.Series) -> dict:
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        parsed = series
    else:
        parsed = parse_datetimes(series)
    values = parsed.dropna()
    stats: dict = {
        "min_date": None,
        "max_date": None,
        "range_days": None,
        "unparseable_count": int(parsed.isna().sum()),
    }
    if len(values) == 0:
        return stats
    stats.update(
        min_date=values.min().isoformat(),
        max_date=values.max().isoformat(),
        range_days=int((values.max() - values.min()).days),
    )
    return stats


def _text_stats(series: pd.Series) -> dict:
    values = series.astype(str)
    if len(values) == 0:
        return {"unique_count": 0, "avg_length": None, "min_length": 0, "max_length": 0}
    lengths = values.str.len()
    return {
        "unique_count": int(values.nunique()),
        "avg_length": _finite(lengths.mean(), 2),
        "min_length": int(lengths.min()),
        "max_length": int(lengths.max()),
    }


def compute_column_stats(series: pd.Series, inferred_type: str) -> dict:
    """Statistics for one column, computed over its non-null values."""
    non_null = series.dropna()
    if inferred_type in ("numeric", "mixed"):
        return _numeric_stats(non_null)
    if inferred_type in ("categorical", "boolean"):
        return _categorical_stats(non_null)
    if inferred_type == "datetime":
        return _datetime_stats(non_null)
    return _text_stats(non_null)


def compute_stats(df: pd.DataFrame, type_info: dict[str, dict]) -> dict[str, dict]:
    """Return {column: stats}. Contents vary by the column's inferred_type."""
    return {
        col: compute_column_stats(df[col], type_info[col]["inferred_type"])
        for col in df.columns
    }
