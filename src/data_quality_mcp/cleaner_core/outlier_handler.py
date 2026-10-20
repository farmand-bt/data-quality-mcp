"""Outlier treatment, ported from the Data Quality Analyzer
(`cleaner/outlier_handler.py`). Deviations (planning/milestone-3.md): a
`reset_index` switch, the IQR multiplier as a parameter, and for cap_iqr
`outliers_treated` counts the values actually clipped."""

import numpy as np
import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.exceptions import InvalidInputError


def handle_outliers(
    df: pd.DataFrame,
    column: str,
    indices: list,
    strategy: str,
    *,
    iqr_multiplier: float = config.OUTLIER_IQR_MULTIPLIER,
    reset_index: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Return a new DataFrame with outliers treated and a log entry.

    indices: row labels of the outliers (the pipeline computes them on the
    input with profiler_core.outliers, the same function the report uses).

    strategies:
        remove      — drop outlier rows entirely
        cap_iqr     — clip values to [Q1 − k×IQR, Q3 + k×IQR] (winsorization)
        replace_nan — replace outlier cell values with NaN (treat as missing later)

    Never modifies the input DataFrame.
    """
    out = df.copy()
    # Only act on indices still present (a previous step might have removed some rows)
    valid_indices = [i for i in indices if i in out.index]
    n_treated = len(valid_indices)
    rows_before = len(out)

    if strategy == "remove":
        out = out.drop(index=valid_indices)
        if reset_index:
            out = out.reset_index(drop=True)

    elif strategy == "cap_iqr":
        vals = pd.to_numeric(out[column], errors="coerce")
        # Fences from finite values, as in profiler/outliers.py; clipping then
        # caps +/-inf to the fences instead of letting inf distort them
        finite = vals[np.isfinite(vals.to_numpy(dtype=float, na_value=np.nan))]
        q1 = float(finite.quantile(0.25))
        q3 = float(finite.quantile(0.75))
        iqr = q3 - q1
        lo = q1 - iqr_multiplier * iqr
        hi = q3 + iqr_multiplier * iqr
        clipped = vals.clip(lower=lo, upper=hi)
        n_treated = int((vals.notna() & (clipped != vals)).sum())
        out[column] = clipped

    elif strategy == "replace_nan":
        out.loc[valid_indices, column] = float("nan")

    else:
        raise InvalidInputError(f"Unknown strategy '{strategy}'")

    return out, {
        "action": "handle_outliers",
        "column": column,
        "strategy": strategy,
        "outliers_treated": n_treated,
        "rows_before": rows_before,
        "rows_after": len(out),
        "rows_removed": rows_before - len(out),
    }
