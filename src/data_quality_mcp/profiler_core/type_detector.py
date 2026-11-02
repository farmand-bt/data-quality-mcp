"""Infer the semantic type of each column, independent of its storage dtype.

Ported and simplified from the Data Quality Analyzer. Works with both pandas 2
(strings stored as `object`) and pandas 3 (strings stored as `str`).
"""

import re
import warnings

import numpy as np
import pandas as pd

from data_quality_mcp import config

# Fraction of non-null values that must parse for a column to count as that type
_NUMERIC_THRESHOLD = 0.80
_DATETIME_THRESHOLD = 0.80
# Distinct values parsed per round when checking a column for dates
_DATE_CHUNK = 1_000
_BOOL_VALUES = {"true", "false", "yes", "no", "y", "n"}
# ISO 8601 date, optionally with a time and then a timezone (Z or +HH:MM); the
# timezone can only follow a time, so "2020-01-15" never reads as an offset
_ISO_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?P<tz>Z|[+-]\d{2}:\d{2})?)?",
    re.IGNORECASE,
)


def parse_datetimes(series: pd.Series) -> pd.Series:
    """Parse values as datetimes (NaT where unparseable), tolerating mixed formats.

    Timestamps with different UTC offsets (e.g. "...Z" and "...+02:00") can't
    share one timezone-aware dtype: pandas 3 raises, and pandas 2 returns an
    object column. They are parsed to UTC then. This only feeds type detection
    and stats, so naive values among them being read as UTC doesn't change the
    data.
    """
    parsed = _parse_mixed(series)
    if parsed is not None:
        return parsed
    # The plain fallback infers one format from the first value and would
    # silently null differently formatted ones, so it comes last
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            parsed = pd.to_datetime(series, errors="coerce")
        except (TypeError, ValueError):
            parsed = None
    if parsed is not None and pd.api.types.is_datetime64_any_dtype(parsed.dtype):
        return parsed
    return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")


def _parse_mixed(series: pd.Series) -> pd.Series | None:
    """parse_datetimes without its last fallback: None if neither format="mixed"
    option gives datetimes. format="mixed" parses each value individually, so
    a column mixing ISO dates, MM/DD/YYYY and "15-Jan-2020" still parses, and
    a value parses the same whatever else is in the series."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for options in ({"format": "mixed"}, {"format": "mixed", "utc": True}):
            try:
                parsed = pd.to_datetime(series, errors="coerce", **options)
            except (TypeError, ValueError):
                continue
            if pd.api.types.is_datetime64_any_dtype(parsed.dtype):
                return parsed
    return None


def has_inconsistent_casing(series: pd.Series) -> bool:
    """True if some labels differ only by case or surrounding whitespace."""
    non_null = series.dropna().astype(str)
    return non_null.str.strip().str.lower().nunique() < non_null.nunique()


def infer_type(
    series: pd.Series,
    *,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
    iso_dates_typed: bool = False,
) -> tuple[str, bool]:
    """Return (inferred_type, type_mismatch) for a single column.

    inferred_type is one of: numeric, categorical, datetime, boolean, text, mixed.
    type_mismatch is True when the values' semantic type differs from how they
    are stored (e.g. numbers stored as strings). With `iso_dates_typed` (set
    for CSV input, which has no date type), text whose values are all ISO 8601
    dates in one timezone convention is not a mismatch.
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
        # Parse each distinct value once (in order of first appearance, so a
        # fallback that infers the format from the first value sees the same
        # one), then weight by how often it occurs: same ratio, far less work
        # on repetitive columns
        codes, uniques = pd.factorize(non_null)
        uniques = pd.Series(uniques, dtype=object)
        parsed = _date_mask(uniques, np.bincount(codes, minlength=len(uniques)))
        if parsed is not None and float(parsed[codes].mean()) >= _DATETIME_THRESHOLD:
            # CSV has no date type, so ISO text is how a CSV stores dates
            # (planning/milestone-3.5.md, D4)
            iso = iso_dates_typed and _all_iso(uniques)
            return "datetime", not iso

    cardinality_ratio = non_null.nunique() / len(non_null)
    if cardinality_ratio <= high_cardinality_threshold:
        return "categorical", False
    return "text", False


def _date_mask(uniques: pd.Series, counts: np.ndarray) -> np.ndarray | None:
    """Which distinct values parse as datetimes (as parse_datetimes would
    parse them), or None once the rows whose value failed are too many for the
    column to reach _DATETIME_THRESHOLD. `counts` is each value's row count.

    Values are parsed in chunks, which gives the same result because
    format="mixed" parses each value on its own: free text, where nearly every
    value fails, is ruled out after about a fifth of its values instead of all
    of them. A chunk that needs parse_datetimes' last fallback, which infers
    one format for the whole column, means parsing everything at once instead.
    """
    total = int(counts.sum())
    parsed = np.zeros(len(uniques), dtype=bool)
    failed = 0
    for start in range(0, len(uniques), _DATE_CHUNK):
        stop = start + _DATE_CHUNK
        chunk = _parse_mixed(uniques.iloc[start:stop])
        if chunk is None:
            return parse_datetimes(uniques).notna().to_numpy()
        parsed[start:stop] = chunk.notna().to_numpy()
        failed += int(counts[start:stop][~parsed[start:stop]].sum())
        if (total - failed) / total < _DATETIME_THRESHOLD:
            return None  # even if every other value parsed
    return parsed


def _all_iso(values: pd.Series) -> bool:
    """Every value is an ISO 8601 date or timestamp that parses, and either all
    of them carry a timezone or none does (a mix is ambiguous). Callers pass
    the distinct values."""
    text = values.astype(str).str.strip()
    matches = text.str.fullmatch(_ISO_DATETIME.pattern, case=False)
    if not matches.all():
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(text, format="ISO8601", utc=True, errors="coerce")
    if parsed.isna().any():
        return False
    has_tz = text.str.extract(_ISO_DATETIME, expand=True)["tz"].notna()
    return bool(has_tz.all() or not has_tz.any())


def detect_types(
    df: pd.DataFrame,
    *,
    high_cardinality_threshold: float = config.HIGH_CARDINALITY_THRESHOLD,
    iso_dates_typed: bool = False,
) -> dict[str, dict]:
    """Return {column: {"pandas_dtype", "inferred_type", "type_mismatch"}}."""
    result = {}
    for col in df.columns:
        inferred_type, type_mismatch = infer_type(
            df[col],
            high_cardinality_threshold=high_cardinality_threshold,
            iso_dates_typed=iso_dates_typed,
        )
        result[col] = {
            "pandas_dtype": str(df[col].dtype),
            "inferred_type": inferred_type,
            "type_mismatch": type_mismatch,
        }
    return result
