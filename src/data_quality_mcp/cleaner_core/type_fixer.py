"""Type conversions, ported from the Data Quality Analyzer
(`cleaner/type_fixer.py`). One deviation: datetime conversion of timestamps
with mixed UTC offsets (see _to_datetime_fallback)."""

import warnings

import pandas as pd

from data_quality_mcp.profiler_core.exceptions import InvalidInputError

_BOOL_MAP = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
    "y": True,
    "n": False,
    "1": True,
    "0": False,
}


def fix_types(
    df: pd.DataFrame,
    column: str,
    target_type: str,
) -> tuple[pd.DataFrame, dict]:
    """Return a new DataFrame with the column cast to target_type and a log entry.

    target_type:
        "numeric"     — strip commas/currency, then pd.to_numeric (coerce on failure)
        "datetime"    — pd.to_datetime with format='mixed' (coerce on failure);
                        mixed UTC offsets become UTC, and a mix of values
                        with and without a timezone is rejected
        "categorical" — pandas Categorical dtype
        "string"      — object/string, preserving NaN
        "boolean"     — map common boolean strings (true/yes/1 → True, etc.)
        "lowercase"   — strip and lowercase labels, then categorical

    Never modifies the input DataFrame.
    """
    out = df.copy()
    original_dtype = str(out[column].dtype)

    if target_type == "numeric":
        cleaned = (
            out[column]
            .astype(str)
            .str.replace(r"[,$%€£\s]", "", regex=True)
            .replace("nan", float("nan"))
        )
        out[column] = pd.to_numeric(cleaned, errors="coerce")

    elif target_type == "datetime":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            # pandas 2 warns about mixed timezones before returning objects
            warnings.simplefilter("ignore", FutureWarning)
            try:
                parsed = pd.to_datetime(out[column], format="mixed", errors="coerce")
            except Exception:
                parsed = None
            # pandas 2 returns an object column for mixed UTC offsets, where
            # pandas 3 raises: both go to the fallback
            if parsed is None or not pd.api.types.is_datetime64_any_dtype(parsed):
                parsed = _to_datetime_fallback(out[column], column)
            out[column] = parsed

    elif target_type == "categorical":
        out[column] = out[column].astype("category")

    elif target_type == "string":
        # Preserve NaN; convert everything else to str
        out[column] = out[column].where(out[column].isna(), out[column].astype(str))

    elif target_type == "boolean":
        out[column] = out[column].astype(str).str.strip().str.lower().map(_BOOL_MAP)

    elif target_type == "lowercase":
        out[column] = (
            out[column]
            .where(out[column].isna(), out[column].astype(str).str.strip().str.lower())
            .astype("category")
        )

    else:
        raise InvalidInputError(f"Unknown target_type '{target_type}'")

    return out, {
        "action": "fix_type",
        "column": column,
        "strategy": f"to_{target_type}",
        "original_dtype": original_dtype,
        "new_dtype": str(out[column].dtype),
        "rows_before": len(df),
        "rows_after": len(out),
        "rows_removed": 0,
    }


def _timezone_awareness(series: pd.Series) -> set[bool]:
    """Whether each distinct value that parses as a timestamp carries a
    timezone (True) or not (False). Values that don't parse are left out:
    they become null anyway, as the docstring promises."""
    awareness = set()
    for value in pd.unique(series.dropna().astype(str).str.strip()):
        try:
            awareness.add(pd.Timestamp(value).tzinfo is not None)
        except (ValueError, TypeError, OverflowError):
            continue
    return awareness


def _to_datetime_fallback(series: pd.Series, column: str) -> pd.Series:
    """The analyzer's fallback, plus one deviation for timezone offsets.

    Timestamps with different UTC offsets can't share one timezone-aware
    dtype (pandas 3 raises, so the analyzer crashes; pandas 2 leaves objects),
    so they are converted to UTC, which keeps every instant. A mix of
    timestamps with and without a timezone is ambiguous, so it's rejected
    instead of guessing one (the analyzer's fallback would silently null some
    of them).
    """
    awareness = _timezone_awareness(series)
    if awareness == {True}:
        return pd.to_datetime(series, format="mixed", errors="coerce", utc=True)
    if True not in awareness:
        return pd.to_datetime(series, errors="coerce")
    raise InvalidInputError(
        f"'{column}' mixes timestamps with and without a timezone (a UTC offset "
        "such as +02:00, or Z), so converting it would mean guessing a timezone "
        "for some of them. Give every value an offset, or none, then convert."
    )
