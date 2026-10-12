"""Convert pandas/numpy values into strictly JSON-serializable Python values."""

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd


def to_json_safe(value: Any) -> Any:
    """Recursively convert `value` so `json.dumps(..., allow_nan=False)` succeeds.

    - numpy scalars become native int/float/bool
    - NaN, +/-inf, None, NaT and pd.NA become None
    - timestamps and dates become ISO 8601 strings
    - dict keys become strings; tuples, lists and arrays become lists
    """
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()  # np.datetime64("NaT").item() is None
        if value is None:
            return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (int, str)):
        return value
    return str(value)
