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


class OutputExistsError(DataQualityError):
    """The requested output file already exists (cleaning never overwrites)."""


class OutputWriteError(DataQualityError):
    """The cleaned output could not be written (no partial file is left)."""


FILE_PATH_NOT_ALLOWED = (
    "Over HTTP this server can't read or write files on its own disk. Send the "
    "data itself: inline_content with the file's contents (CSV as text, .xlsx as "
    "base64) and filename_hint with its name."
)


class FilePathNotAllowedError(DataQualityError):
    """A file path was given where file access is off (over HTTP, M4 D3)."""

    def __init__(self, message: str = FILE_PATH_NOT_ALLOWED) -> None:
        super().__init__(message)
