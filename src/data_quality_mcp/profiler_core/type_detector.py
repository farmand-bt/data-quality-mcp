"""Infer the semantic type of each column, independent of its storage dtype.

Ported and simplified from the Data Quality Analyzer. Works with both pandas 2
(strings stored as `object`) and pandas 3 (strings stored as `str`).
"""

import warnings

import numpy as np
import pandas as pd

from data_quality_mcp import config

# Fraction of non-null values that must parse for a column to count as that type
_NUMERIC_THRESHOLD = 0.80
_DATETIME_THRESHOLD = 0.80
_BOOL_VALUES = {"true", "false", "yes", "no", "y", "n"}


def parse_datetimes(series: pd.Series) -> pd.Series:
    """Parse values as datetimes (NaT where unparseable), tolerating mixed formats."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            # format="mixed" parses each value individually, so a column mixing
            # ISO dates, MM/DD/YYYY and "15-Jan-2020" still parses.
            return pd.to_datetime(series, format="mixed", errors="coerce")
        except (TypeError, ValueError):
            return pd.to_datetime(series, errors="coerce")


def has_inconsistent_casing(series: pd.Series) -> bool:
    """True if some labels differ only by case or surrounding whitespace."""
    non_null = series.dropna().astype(str)
    return non_null.str.strip().str.lower().nunique() < non_null.nunique()


def infer_type(
    series: pd.Series,
    *,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
) -> tuple[str, bool]:
    """Return (inferred_type, type_mismatch) for a single column.

    inferred_type is one of: numeric, categorical, datetime, boolean, text, mixed.
    type_mismatch is True when the values' semantic type differs from how they
    are stored (e.g. numbers stored as strings).
    """
    dtype = series.dtype

    if pd.api.types.is_bool_dtype(dtype):
        return "boolean", False
    if pd.api.types.is_numeric_dtype(dtype):
        return "numeric", False
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime", False

    non_null = series.dropna()
    if len(non_null) == 0:
        return "categorical", False

    # Real bools in an object column (pandas does this for a True/False column
    # with missing cells): boolean, and not a storage mismatch.
    if all(isinstance(v, (bool, np.bool_)) for v in non_null):
        return "boolean", False

    # Boolean-like strings; require 2+ distinct values to avoid false positives
    unique_lower = set(non_null.astype(str).str.strip().str.lower().unique())
    if unique_lower <= _BOOL_VALUES and len(unique_lower) >= 2:
        return "boolean", True

    numeric_ratio = float(pd.to_numeric(non_null, errors="coerce").notna().mean())
    if numeric_ratio == 1.0:
        return "numeric", True  # every value parses: numbers stored as text
    if numeric_ratio >= _NUMERIC_THRESHOLD:
        return "mixed", True  # mostly numeric with stray non-numeric values

    # Only try datetimes when the column isn't mostly numeric-looking
    if numeric_ratio < 0.5:
        datetime_ratio = float(parse_datetimes(non_null).notna().mean())
        if datetime_ratio >= _DATETIME_THRESHOLD:
            return "datetime", True

    cardinality_ratio = non_null.nunique() / len(non_null)
    if cardinality_ratio <= high_cardinality_threshold:
        return "categorical", False
    return "text", False


def detect_types(
    df: pd.DataFrame,
    *,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
) -> dict[str, dict]:
    """Return {column: {"pandas_dtype", "inferred_type", "type_mismatch"}}."""
    result = {}
    for col in df.columns:
        inferred_type, type_mismatch = infer_type(
            df[col], high_cardinality_threshold=high_cardinality_threshold
        )
        result[col] = {
            "pandas_dtype": str(df[col].dtype),
            "inferred_type": inferred_type,
            "type_mismatch": type_mismatch,
        }
    return result
