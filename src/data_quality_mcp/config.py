"""Default settings for the profiling and cleaning engines.

These are defaults only. Core functions accept each value as a keyword
parameter, so a caller (e.g. a remote deployment) can override limits without
touching the core. This module must stay free of any `mcp` import.
"""

# --- Resource limits (enforced by profiler_core.loader) ---
MAX_FILE_SIZE_MB = 50
MAX_ROWS = 500_000
# An XLSX is a zip: cap what it may expand to, and how many entries it may
# have, before parsing it (shared strings are held in memory in full).
MAX_XLSX_UNCOMPRESSED_MB = 200
MAX_XLSX_ENTRIES = 1000
# UNC and device paths (\\server\share, \\?\, \\.\) are rejected on Windows:
# opening one connects to that host over SMB and can leak NTLM credentials.
ALLOW_NETWORK_PATHS = False

# --- Cleaning (cleaner_core) ---
MAX_CLEANING_STEPS = 50
MAX_INLINE_OUTPUT_MB = 1  # Inline output goes into the model's context
CLEANED_SUFFIX = "_cleaned"  # <stem>_cleaned.<ext>, then _cleaned_2, ...

# --- Profiling thresholds ---
MISSING_THRESHOLD = 0.30  # Column warning when more than this fraction is missing
HIGH_CARDINALITY_THRESHOLD = 0.5  # Unique ratio above this => free text, not category
OUTLIER_IQR_MULTIPLIER = 1.5
OUTLIER_ZSCORE_THRESHOLD = 3.0  # classic |x - mean| / std, as in the analyzer
OUTLIER_COLUMN_WARNING_PCT = 5.0  # Column warning when IQR outliers exceed this %
HIGH_CORRELATION_THRESHOLD = 0.9

# --- Result-size and runtime bounds (keep tool results small for agents) ---
MAX_REPORTED_INDICES = 50  # Row-index lists are capped; counts stay exact
MAX_CORRELATION_NUMERIC_COLUMNS = 50  # Pearson uses the first N numeric columns
MAX_CRAMERS_V_COLUMNS = 15  # Cramér's V uses the first N categorical columns...
MAX_CRAMERS_V_CARDINALITY = 50  # ...that have at most this many distinct values
MAX_NEAR_DUPLICATE_COLUMNS = 50  # Near-duplicate search is skipped above this

# --- Quality grading (score >= threshold => grade; below the lowest => "F") ---
QUALITY_GRADE_THRESHOLDS = {"A": 90, "B": 80, "C": 70, "D": 60}
