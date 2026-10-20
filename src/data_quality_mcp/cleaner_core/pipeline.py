"""Run a list of cleaning steps on a DataFrame, non-destructively, with a log.

Ported from the Data Quality Analyzer: `cleaner/pipeline.py` for execution and
`app/components/cleaning_panel.py` for the step order and the per-column
choices its UI allows. Deviations (planning/milestone-3.md):
- Validation (D6) runs in the core, because an agent has no dropdown.
- Original row labels are kept until every step has run, then the index is
  reset once. The analyzer resets after each removal, so a second outlier
  `remove` step deleted the wrong row.
- Outlier rows come from the input, through the same function as the report.
- One uniform log entry per step, with `values_nulled`.
"""

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.cleaner_core.duplicate_handler import remove_duplicates
from data_quality_mcp.cleaner_core.missing_handler import handle_missing
from data_quality_mcp.cleaner_core.outlier_handler import handle_outliers
from data_quality_mcp.cleaner_core.schema import (
    CleaningStep,
    FixTypeStep,
    HandleMissingStep,
    HandleOutliersStep,
    RemoveDuplicatesStep,
)
from data_quality_mcp.cleaner_core.type_fixer import fix_types
from data_quality_mcp.profiler_core.exceptions import InvalidInputError
from data_quality_mcp.profiler_core.outliers import detect_column_outliers
from data_quality_mcp.profiler_core.schema import CleaningLogEntry
from data_quality_mcp.profiler_core.type_detector import infer_type

_LOG_FIELDS = (
    "action",
    "column",
    "strategy",
    "rows_before",
    "rows_after",
    "rows_removed",
    "values_nulled",
    "original_dtype",
    "new_dtype",
    "missing_before",
    "missing_after",
    "outliers_treated",
)
_MIN_FINITE_FOR_FENCES = 4
_STEP_TYPES = {
    "handle_missing": HandleMissingStep,
    "remove_duplicates": RemoveDuplicatesStep,
    "fix_type": FixTypeStep,
    "handle_outliers": HandleOutliersStep,
}


def step_priority(step: CleaningStep) -> int:
    """The analyzer's order: 0 = no row change (type fixes, fills, cap_iqr,
    replace_nan), 1 = outlier remove, 2 = row and column drops. Outlier rows
    come from the input, so they stay valid when no row has been dropped yet."""
    action = step["action"]
    strategy = step.get("strategy", "")
    if action == "handle_outliers" and strategy == "remove":
        return 1
    if action == "remove_duplicates" or (
        action == "handle_missing" and strategy in ("drop_rows", "drop_column")
    ):
        return 2
    return 0


def run_steps(
    df: pd.DataFrame,
    steps: Sequence[CleaningStep],
    *,
    iqr_multiplier: float = config.OUTLIER_IQR_MULTIPLIER,
    zscore_threshold: float = config.OUTLIER_ZSCORE_THRESHOLD,
) -> tuple[pd.DataFrame, list[CleaningLogEntry]]:
    """Validate `steps`, run them in the analyzer's order on a copy of `df`,
    and return the cleaned frame (0-based index) and the log.

    `df` must have a 0-based RangeIndex (profiler_core.report.prepare_frame),
    so its labels are the row positions the report shows.
    """
    outlier_rows = _validate(df, steps, iqr_multiplier, zscore_threshold)
    out = df.copy()
    log: list[CleaningLogEntry] = []
    for step in sorted(steps, key=step_priority):  # stable: keeps caller order
        before = out
        try:
            out, raw = _apply(out, step, outlier_rows, iqr_multiplier)
        except (TypeError, ValueError) as exc:
            # pandas rejecting an operation on this column's dtype: tell the
            # caller which step failed instead of a generic tool crash
            target = f" on '{step['column']}'" if "column" in step else ""
            raise InvalidInputError(
                f"The {step['action']} step{target} failed ({exc}). Try another "
                "strategy, or a fix_type step first."
            ) from exc
        log.append(_log_entry(raw, before, out))
    if len(out) == 0 or len(out.columns) == 0:
        raise InvalidInputError(
            "These steps would remove every "
            + ("row" if len(out) == 0 else "column")
            + ", leaving nothing to write. Use less aggressive steps (for "
            "example fill instead of drop_rows)."
        )
    return out.reset_index(drop=True), log


# --- validation before the first step (planning/milestone-3.md, D6) ---


def _validate(
    df: pd.DataFrame,
    steps: Sequence[CleaningStep],
    iqr_multiplier: float,
    zscore_threshold: float,
) -> dict[str, dict[str, list[int]]]:
    """Check the static rules; return the input's outlier rows per column and
    method for every handle_outliers step."""
    seen: set[tuple[str, str]] = set()
    dropped = {
        s["column"]
        for s in steps
        if s["action"] == "handle_missing" and s["strategy"] == "drop_column"
    }
    outlier_rows: dict[str, dict[str, list[int]]] = {}

    for step in steps:
        _validate_keys(step)
        action = step["action"]
        if action == "remove_duplicates":
            if ("remove_duplicates", "") in seen:
                raise InvalidInputError("Only one remove_duplicates step is allowed.")
            seen.add(("remove_duplicates", ""))
            continue

        column = step["column"]
        if column not in df.columns:
            raise InvalidInputError(
                f"Column '{column}' doesn't exist. Columns: "
                + ", ".join(repr(c) for c in list(df.columns)[:30])
                + (" ..." if len(df.columns) > 30 else "")
            )
        if (action, column) in seen:
            raise InvalidInputError(
                f"Only one {action} step per column is allowed ('{column}' has "
                "two), as in the analyzer's cleaning panel: pick one strategy."
            )
        seen.add((action, column))
        if action in ("fix_type", "handle_outliers") and column in dropped:
            raise InvalidInputError(
                f"Column '{column}' is removed by a drop_column step, so this "
                f"{action} step would have no effect. Remove one of them."
            )

        if action == "handle_missing":
            _validate_value(step)
        elif action == "handle_outliers":
            method = step.get("method", "iqr")
            if step["strategy"] == "cap_iqr" and method == "zscore":
                raise InvalidInputError(
                    "cap_iqr always clips to the IQR fences, so method 'zscore' "
                    "has no effect with it. Omit 'method' or use 'iqr'."
                )
            # The same gate as profiler_core.outliers.detect_outliers, so a step
            # is accepted exactly where detect_outliers reports a result
            inferred, _ = infer_type(df[column])
            info = (
                detect_column_outliers(
                    df[column],
                    len(df),
                    iqr_multiplier=iqr_multiplier,
                    zscore_threshold=zscore_threshold,
                    max_indices=len(df),
                )
                if inferred in ("numeric", "mixed")
                else None
            )
            if info is None:
                raise InvalidInputError(
                    f"Outlier detection doesn't apply to '{column}' in the input "
                    "(it isn't numeric, or has fewer than 4 numbers). If a "
                    "fix_type step makes it numeric, run that in this call, then "
                    "treat its outliers with a second clean_dataset call on the "
                    "output."
                )
            outlier_rows[column] = {
                "iqr": info["iqr_indices"],
                "zscore": info["zscore_indices"],
            }
    return outlier_rows


def _validate_keys(step: CleaningStep) -> None:
    """Unknown keys are an error, not silently ignored: a `column` on
    remove_duplicates or a misspelt `method` would otherwise change the result
    without a word (D6: an option with no effect is an error)."""
    shape = _STEP_TYPES.get(step.get("action"))
    if shape is None:
        raise InvalidInputError(
            f"Unknown action {step.get('action')!r}. Use one of: "
            + ", ".join(_STEP_TYPES)
            + "."
        )
    allowed = shape.__required_keys__ | shape.__optional_keys__
    unknown = sorted(set(step) - allowed)
    if unknown:
        raise InvalidInputError(
            f"{step['action']} doesn't take {', '.join(map(repr, unknown))}. "
            f"Its keys are: {', '.join(sorted(allowed))}."
        )
    missing = sorted(shape.__required_keys__ - set(step))
    if missing:
        raise InvalidInputError(
            f"{step['action']} needs {', '.join(map(repr, missing))}."
        )


def _validate_value(step: CleaningStep) -> None:
    has_value = step.get("value") is not None
    if step["strategy"] == "fill_constant":
        if not has_value:
            raise InvalidInputError(
                "fill_constant needs a non-null 'value' (filling missing values "
                "with null would change nothing)."
            )
        value = step["value"]
        if isinstance(value, str) and not value.strip():
            raise InvalidInputError(
                "fill_constant's 'value' is empty. An empty string is written "
                "as an empty cell, which reads back as missing; give a real value."
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidInputError(
                "fill_constant's 'value' must be a finite number, not NaN or Infinity."
            )
    elif "value" in step:
        raise InvalidInputError(
            f"'value' only applies to fill_constant, not to {step['strategy']}. "
            "Remove it."
        )


# --- running one step ---


def _apply(
    out: pd.DataFrame,
    step: CleaningStep,
    outlier_rows: dict[str, dict[str, list[int]]],
    iqr_multiplier: float,
) -> tuple[pd.DataFrame, dict]:
    action = step["action"]
    if action == "remove_duplicates":
        return remove_duplicates(out, step.get("keep", "first"), reset_index=False)

    column = step["column"]
    if action == "fix_type":
        if step["target_type"] == "lowercase":
            inferred, _ = infer_type(out[column])
            if inferred != "categorical":
                raise InvalidInputError(
                    f"lowercase only applies to categorical columns, and "
                    f"'{column}' is {inferred}. It's meant for labels like "
                    "Male/MALE/male."
                )
        return fix_types(out, column, step["target_type"])

    if action == "handle_missing":
        strategy = step["strategy"]
        value = None
        if strategy in ("fill_mean", "fill_median"):
            _require_numbers(out[column], column, strategy)
        elif strategy == "fill_constant":
            value = _fill_value(out[column], column, step["value"])
        return handle_missing(out, strategy, column, value, reset_index=False)

    strategy = step["strategy"]
    if strategy == "cap_iqr":
        values = pd.to_numeric(out[column], errors="coerce").to_numpy(
            dtype=float, na_value=np.nan
        )
        if int(np.isfinite(values).sum()) < _MIN_FINITE_FOR_FENCES:
            raise InvalidInputError(
                f"cap_iqr needs at least {_MIN_FINITE_FOR_FENCES} finite numbers "
                f"in '{column}' to compute fences. Use remove or replace_nan."
            )
    rows = outlier_rows[column][step.get("method", "iqr")]
    return handle_outliers(
        out, column, rows, strategy, iqr_multiplier=iqr_multiplier, reset_index=False
    )


def _require_numbers(series: pd.Series, column: str, strategy: str) -> None:
    """The analyzer's UI offers fill_mean/fill_median only for numeric/mixed
    columns; with no numbers at all its handler crashes on round(nan)."""
    inferred, _ = infer_type(series)
    if inferred not in ("numeric", "mixed") or not (
        pd.to_numeric(series, errors="coerce").notna().any()
    ):
        raise InvalidInputError(
            f"{strategy} needs a numeric column with at least one number, and "
            f"'{column}' is {inferred}. Use fill_mode or fill_constant, or list a "
            "fix_type step to numeric before this one."
        )


def _is_boolean(series: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(series.dtype):
        return True
    non_null = series.dropna()
    return len(non_null) > 0 and all(isinstance(v, (bool, np.bool_)) for v in non_null)


def _fill_value(series: pd.Series, column: str, value):
    """Convert fill_constant's value to the column's type (D6)."""
    if _is_boolean(series):
        if not isinstance(value, bool):
            raise InvalidInputError(
                f"'{column}' is a boolean column, so fill_constant needs true or false."
            )
        return value
    if pd.api.types.is_numeric_dtype(series.dtype):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidInputError(
                f"'{column}' is numeric, so fill_constant needs a number "
                f"(got {value!r})."
            )
        return value
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        try:
            timestamp = pd.Timestamp(str(value))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(
                f"'{column}' holds dates, so fill_constant needs a date string "
                f"such as '2024-01-31' (got {value!r})."
            ) from exc
        if (timestamp.tz is None) != (series.dt.tz is None):
            raise InvalidInputError(
                f"The fill value's timezone doesn't match '{column}': give a date "
                + (
                    "without a timezone."
                    if series.dt.tz is None
                    else "with a timezone offset."
                )
            )
        return timestamp
    return str(value)


def _log_entry(
    raw: dict, before: pd.DataFrame, after: pd.DataFrame
) -> CleaningLogEntry:
    entry = {field: raw.get(field) for field in _LOG_FIELDS}
    column = raw.get("column")
    if column is not None and column in after.columns:
        was_present = before[column].notna().reindex(after.index, fill_value=False)
        entry["values_nulled"] = int((was_present & after[column].isna()).sum())
    return entry
