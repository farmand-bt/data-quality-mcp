"""Exact and near-duplicate row detection.

Ported from the Data Quality Analyzer (`profiler/duplicates.py`) with the same
definitions. Deviations (see planning/milestone-2.md): index samples are
sorted for deterministic output and both lists share one cap, reported via
`indices_truncated`. Added in Milestone 3.5 (D3): `key_columns`, identifier
columns whose values repeat on rows with different data.
"""

import re

import numpy as np
import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import DuplicateInfo, KeyColumnInfo

# A key column's name: exactly id/key/uuid/guid in any case, or ending in one
# after _ - or a space (any case), or after a camelCase boundary (a lowercase
# letter or digit, then the word with a capital: orderKey, CustomerID). The
# boundary needs that change of case, so "paid", "valid" and "ORDERID" don't
# match.
_KEY_WORD = r"(?:id|key|uuid|guid)"
_KEY_NAME = re.compile(
    rf"(?i:^{_KEY_WORD}$|[_\-\s]{_KEY_WORD}$)"
    r"|(?<=[a-z0-9])(?:Id|ID|Key|KEY|Uuid|UUID|Guid|GUID)$"
)

# Odd multipliers make each column's hash contribution distinct, so rows whose
# values are merely permuted across columns rarely produce the same key.
_COLUMN_MIX = np.uint64(0x9E3779B97F4A7C15)


def _near_duplicate_rows(df: pd.DataFrame) -> set[int]:
    """Rows equal to at least one other row on every column but one.

    Same result as running `df.duplicated(subset=<all but c>, keep=False)` for
    each column c, but each cell is hashed once instead of every column being
    re-compared per pass. A row's key for "all columns except c" is the
    (wrapping) sum of its per-column hashes minus column c's term, so each pass
    only deduplicates one uint64 array. Hash equality is only a filter: every
    candidate group is confirmed on the real values, so a hash collision can
    never add a false near-duplicate.

    Columns and rows are addressed by position, so duplicate column names or a
    non-unique index can't select the wrong data. Returns labels of `df.index`.
    """
    n_rows, n_cols = df.shape
    # One preallocated (n, k) array filled in place keeps the peak memory to a
    # single uint64 matrix (about 200 MB at 500k rows x 50 columns).
    # Array (not scalar) uint64 arithmetic wraps modulo 2**64 silently, which
    # is intended; numpy only warns about overflow for scalar operations.
    mixers = (
        np.arange(n_cols, dtype=np.uint64) * np.uint64(2) + np.uint64(1)
    ) * _COLUMN_MIX
    terms = np.empty((n_rows, n_cols), dtype=np.uint64)
    for j in range(n_cols):
        hashes = pd.util.hash_pandas_object(df.iloc[:, j], index=False)
        np.multiply(hashes.to_numpy(np.uint64), mixers[j : j + 1], out=terms[:, j])
    total = terms.sum(axis=1, dtype=np.uint64)

    rows: set[int] = set()
    for j in range(n_cols):
        candidates = np.flatnonzero(
            pd.Series(total - terms[:, j]).duplicated(keep=False).to_numpy()
        )
        if len(candidates) == 0:
            continue
        others = [k for k in range(n_cols) if k != j]
        subset = df.iloc[candidates, others]
        confirmed = candidates[subset.duplicated(keep=False).to_numpy()]
        rows.update(int(i) for i in df.index[confirmed])
    return rows


def is_key_name(name: str) -> bool:
    """Whether a column name says it's an identifier (see _KEY_NAME)."""
    return _KEY_NAME.search(name.strip()) is not None


def key_columns(
    df: pd.DataFrame,
    *,
    min_unique_ratio: float = config.KEY_MIN_UNIQUE_RATIO,
    max_indices: int = config.MAX_REPORTED_INDICES,
    kept: pd.Series | None = None,
) -> list[KeyColumnInfo]:
    """Identifier-like columns, and their values found on rows with different
    data (planning/milestone-3.5.md, D3).

    A key column has a key-like name and, on the rows left after
    drop_duplicates(keep="first"), at least `min_unique_ratio` distinct
    non-null values (so foreign keys, which repeat by design, are left out).
    Conflicts are counted on those rows with null keys ignored, so exact
    duplicates, already in exact_count, aren't counted again. A key column
    with no conflicts is listed with zeros.
    """
    names = [(i, name) for i, name in enumerate(df.columns) if is_key_name(str(name))]
    if df.empty or not names:
        return []
    if kept is None:
        kept = ~df.duplicated(keep="first")
    result: list[KeyColumnInfo] = []
    for position, name in names:
        values = df.iloc[kept.to_numpy(), position].dropna()
        if len(values) == 0 or values.nunique() / len(values) < min_unique_ratio:
            continue
        repeated = values.duplicated(keep=False)
        rows = sorted(int(i) for i in values.index[repeated.to_numpy()])
        result.append(
            {
                "column": str(name),
                "conflicting_values": int(values[repeated].nunique()),
                "conflicting_rows": len(rows),
                "sample_indices": rows[:max_indices],
                "indices_truncated": len(rows) > max_indices,
            }
        )
    return result


def analyze_duplicates(
    df: pd.DataFrame,
    *,
    max_indices: int = config.MAX_REPORTED_INDICES,
    max_near_duplicate_columns: int = config.MAX_NEAR_DUPLICATE_COLUMNS,
    key_min_unique_ratio: float = config.KEY_MIN_UNIQUE_RATIO,
    kept: pd.Series | None = None,
) -> DuplicateInfo:
    """Exact duplicates and near-duplicates (identical on all columns but one).

    exact_count is the number of rows that would be dropped keeping the first
    copy; sample_indices lists every row in a duplicate group. Near-duplicate
    search is skipped for fewer than 2 or more than `max_near_duplicate_columns`
    columns, and excludes rows that are already exact duplicates.

    Indices are labels of `df.index`; callers pass a 0-based RangeIndex (as
    report.py does) so they are row positions.
    """
    n = len(df)
    if n == 0:
        return {
            "exact_count": 0,
            "exact_pct": 0.0,
            "sample_indices": [],
            "near_duplicate_count": 0,
            "near_duplicate_pct": 0.0,
            "near_sample_indices": [],
            "indices_truncated": False,
            "key_columns": [],
        }

    in_group = df.duplicated(keep=False)  # every copy of a duplicated row
    if kept is None:
        kept = ~df.duplicated(keep="first")
    exact_count = int((~kept).sum())
    exact_rows = sorted(int(i) for i in in_group[in_group].index)

    near_rows: set[int] = set()
    if 2 <= len(df.columns) <= max_near_duplicate_columns:
        near_rows = _near_duplicate_rows(df) - set(exact_rows)
    near_sorted = sorted(near_rows)

    return {
        "exact_count": exact_count,
        "exact_pct": round(exact_count / n * 100, 2),
        "sample_indices": exact_rows[:max_indices],
        "near_duplicate_count": len(near_sorted),
        "near_duplicate_pct": round(len(near_sorted) / n * 100, 2),
        "near_sample_indices": near_sorted[:max_indices],
        "indices_truncated": len(exact_rows) > max_indices
        or len(near_sorted) > max_indices,
        "key_columns": key_columns(
            df,
            min_unique_ratio=key_min_unique_ratio,
            max_indices=max_indices,
            kept=kept,
        ),
    }
