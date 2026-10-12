"""Domain exceptions raised by the profiling core.

These are transport-agnostic. The `tools/` layer is responsible for turning
them into protocol-level errors; the core never raises MCP-specific errors.
Messages are written to be actionable for the caller (often an AI agent).
"""


class DataQualityError(Exception):
    """Base class for all expected, caller-fixable errors in the core."""


class InvalidInputError(DataQualityError):
    """The arguments are invalid (e.g. neither or both inputs, bad base64)."""


class DataFileNotFoundError(DataQualityError):
    """The given file path does not exist or is not a regular file."""


class UnsupportedFormatError(DataQualityError):
    """The file extension is not a supported tabular format."""


class FileTooLargeError(DataQualityError):
    """The input exceeds the configured maximum size."""


class TooManyRowsError(DataQualityError):
    """The dataset exceeds the configured maximum row count."""


class DataParseError(DataQualityError):
    """The input could not be parsed into a table, or the table is empty."""
