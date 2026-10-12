"""Default settings for the profiling and cleaning engines.

These are defaults only. Core functions accept each value as a keyword
parameter, so a caller (e.g. a remote deployment) can override limits without
touching the core. This module must stay free of any `mcp` import.
"""

# --- Resource limits (enforced by profiler_core.loader) ---
MAX_FILE_SIZE_MB = 50
MAX_ROWS = 500_000

# --- Profiling thresholds ---
MISSING_THRESHOLD = 0.30  # Column warning when more than this fraction is missing
HIGH_CARDINALITY_THRESHOLD = 0.5  # Unique ratio above this => free text, not category
OUTLIER_IQR_MULTIPLIER = 1.5
OUTLIER_ZSCORE_THRESHOLD = 3.0
HIGH_CORRELATION_THRESHOLD = 0.9

# --- Quality grading (score >= threshold => grade; below the lowest => "F") ---
QUALITY_GRADE_THRESHOLDS = {"A": 90, "B": 80, "C": 70, "D": 60}
