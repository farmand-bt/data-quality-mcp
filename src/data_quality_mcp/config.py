"""Default settings for the profiling and cleaning engines.

These are defaults only. Core functions accept each value as a keyword
parameter, so a caller (e.g. a remote deployment) can override limits without
touching the core. This module must stay free of any `mcp` import, and of any
web framework: it uses the standard library only.

A few limits can be overridden by `DATA_QUALITY_MCP_*` environment variables,
read here and nowhere else (planning/milestone-4.md, D4). An invalid value
keeps its default and is recorded in CONFIG_ERRORS, which the server's main()
reports before it serves anything. Valid overrides are listed in
CONFIG_OVERRIDES, for the startup log.
"""

import math
import os

ENV_PREFIX = "DATA_QUALITY_MCP_"
CONFIG_ERRORS: list[str] = []
CONFIG_OVERRIDES: dict[str, str] = {}


def _env_limit(name: str, default: int, *, integer: bool = False) -> int | float:
    """`default`, or the positive, finite number in DATA_QUALITY_MCP_<name>.

    An unset, empty or blank variable means the default. A whole number such
    as "20" comes back as an int, so messages read "20 MB", not "20.0 MB".
    """
    variable = ENV_PREFIX + name
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or value <= 0:
        expected = "a positive whole number" if integer else "a positive number"
        CONFIG_ERRORS.append(f"invalid {variable}={raw!r}: expected {expected}")
        return default
    CONFIG_OVERRIDES[variable] = raw
    return int(value) if float(value).is_integer() else value


# --- Resource limits (enforced by profiler_core.loader) ---
MAX_FILE_SIZE_MB = _env_limit("MAX_FILE_SIZE_MB", 50)
MAX_ROWS = int(_env_limit("MAX_ROWS", 500_000, integer=True))
# An XLSX is a zip: cap what it may expand to, and how many entries it may
# have, before parsing it (shared strings are held in memory in full).
MAX_XLSX_UNCOMPRESSED_MB = _env_limit("MAX_XLSX_UNCOMPRESSED_MB", 200)
MAX_XLSX_ENTRIES = 1000
# UNC and device paths (\\server\share, \\?\, \\.\) are rejected on Windows:
# opening one connects to that host over SMB and can leak NTLM credentials.
ALLOW_NETWORK_PATHS = False
# Off over HTTP, where a path would name a file on the server's own disk
# (planning/milestone-4.md, D3). Not overridable from the environment.
ALLOW_FILE_PATH = True
# Largest HTTP request body, inline data included (the SDK's default is 4 MiB)
MAX_HTTP_REQUEST_MB = _env_limit("MAX_HTTP_REQUEST_MB", 16)

# --- Cleaning (cleaner_core) ---
MAX_CLEANING_STEPS = 50
# Inline output goes into the model's context
MAX_INLINE_OUTPUT_MB = _env_limit("MAX_INLINE_OUTPUT_MB", 1)
CLEANED_SUFFIX = "_cleaned"  # <stem>_cleaned.<ext>, then _cleaned_2, ...

# --- Profiling thresholds ---
MISSING_THRESHOLD = 0.30  # Column warning when more than this fraction is missing
HIGH_CARDINALITY_THRESHOLD = 0.5  # Unique ratio above this => free text, not category
OUTLIER_IQR_MULTIPLIER = 1.5
OUTLIER_ZSCORE_THRESHOLD = 3.0  # classic |x - mean| / std, as in the analyzer
OUTLIER_COLUMN_WARNING_PCT = 5.0  # Column warning when IQR outliers exceed this %
HIGH_CORRELATION_THRESHOLD = 0.9
MIN_CORRELATION_ROWS = 10  # A correlation needs this many rows with both values

# --- Heuristics added in Milestone 3.5 (see planning/milestone-3.5.md) ---
# Missing-value associations (D1): which observed columns go with a column's gaps
MAX_MISSING_PATTERN_COLUMNS = 50  # Columns with missing values that are tested
MIN_MISSING_PATTERN_ROWS = 10  # Missing and present rows each pair needs
MISSING_ASSOCIATION_THRESHOLD = 0.3  # Minimum |r|, Cramér's V or phi to list
MISSING_ASSOCIATION_MAX_P = 0.001  # Divided by the pairs tested (Bonferroni)
MAX_MISSING_ASSOCIATIONS = 5  # Listed per column, strongest first
# Possible sentinel (placeholder) values among the outliers (D2)
SENTINEL_MIN_UNIQUE_RATIO = 0.5  # The repeat test skips columns below this
MAX_SENTINELS = 10  # Listed per column
# Identifier columns whose values repeat with different data (D3)
KEY_MIN_UNIQUE_RATIO = 0.95  # Nearly unique, so foreign keys are left out
MAX_KEY_SAMPLE_VALUES = 10  # Conflicting key values listed per column

# --- Result-size and runtime bounds (keep tool results small for agents) ---
MAX_REPORTED_INDICES = 50  # Row-index lists are capped; counts stay exact
MAX_CORRELATION_NUMERIC_COLUMNS = 50  # Pearson uses the first N numeric columns
MAX_CRAMERS_V_COLUMNS = 15  # Cramér's V uses the first N categorical columns...
MAX_CRAMERS_V_CARDINALITY = 50  # ...that have at most this many distinct values
MAX_NEAR_DUPLICATE_COLUMNS = 50  # Near-duplicate search is skipped above this

# --- Quality grading (score >= threshold => grade; below the lowest => "F") ---
QUALITY_GRADE_THRESHOLDS = {"A": 90, "B": 80, "C": 70, "D": 60}
