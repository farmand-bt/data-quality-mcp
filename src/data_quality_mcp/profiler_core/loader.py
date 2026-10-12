"""Load a tabular dataset from a file path or from inline base64 content.

Both input modes exist from Milestone 1 so tool signatures don't need to change
later: `file_path` suits local stdio use, `inline_content` suits remote use
where the server cannot see the caller's filesystem.
"""

import base64
import binascii
import io
from pathlib import Path

import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.exceptions import (
    DataFileNotFoundError,
    DataParseError,
    FileTooLargeError,
    InvalidInputError,
    TooManyRowsError,
    UnsupportedFormatError,
)

SUPPORTED_EXTENSIONS = (".csv", ".xlsx")

# Encodings tried in order for CSV. latin-1 maps every byte, so it never fails
# and acts as the last resort.
_CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
_SEPARATOR_SNIFF_CHARS = 4096


def load_data(
    file_path: str | None = None,
    inline_content: str | None = None,
    filename_hint: str | None = None,
    *,
    max_file_size_mb: float = config.MAX_FILE_SIZE_MB,
    max_rows: int = config.MAX_ROWS,
) -> pd.DataFrame:
    """Load a CSV or XLSX dataset into a DataFrame.

    Exactly one of `file_path` or `inline_content` must be given.

    Args:
        file_path: Path to a .csv or .xlsx file on the local filesystem.
        inline_content: Base64-encoded file content.
        filename_hint: Filename used to infer the format of `inline_content`
            (e.g. "sales.csv"). Required with `inline_content`.
        max_file_size_mb: Reject inputs larger than this.
        max_rows: Reject datasets with more data rows than this.

    Raises:
        InvalidInputError, DataFileNotFoundError, UnsupportedFormatError,
        FileTooLargeError, TooManyRowsError, DataParseError.
    """
    if (file_path is None) == (inline_content is None):
        raise InvalidInputError(
            "Provide exactly one of 'file_path' or 'inline_content', not "
            + ("both." if file_path is not None else "neither.")
        )

    if inline_content is not None:
        raw, extension = _decode_inline(inline_content, filename_hint, max_file_size_mb)
    else:
        raw, extension = _read_file(str(file_path), max_file_size_mb)

    if not raw:
        raise DataParseError("The input is empty (0 bytes).")

    if extension == ".csv":
        df = _parse_csv(raw, max_rows)
    else:
        df = _parse_xlsx(raw, max_rows)

    if len(df) > max_rows:
        raise TooManyRowsError(
            f"The dataset has more than {max_rows:,} rows, which exceeds the "
            "server's limit. Profile a sample or a subset of the data instead."
        )
    if len(df.columns) == 0:
        raise DataParseError("No columns could be read from the input.")
    if len(df) == 0:
        raise DataParseError("The input has a header row but no data rows.")
    return df


def _extension_of(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported file type '{extension or '(none)'}' for '{filename}'. "
            f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}."
        )
    return extension


def _check_size(size_bytes: int, max_file_size_mb: float) -> None:
    if size_bytes > max_file_size_mb * 1024 * 1024:
        raise FileTooLargeError(
            f"The input is {size_bytes / (1024 * 1024):.1f} MB, which exceeds the "
            f"server's limit of {max_file_size_mb:g} MB."
        )


def _read_file(file_path: str, max_file_size_mb: float) -> tuple[bytes, str]:
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise DataFileNotFoundError(
            f"No file found at '{file_path}'. Use an absolute path: relative paths "
            f"are resolved against the server's working directory ({Path.cwd()}), "
            "which is usually not the directory you expect."
        )
    extension = _extension_of(path.name)
    _check_size(path.stat().st_size, max_file_size_mb)
    return path.read_bytes(), extension


def _decode_inline(
    inline_content: str, filename_hint: str | None, max_file_size_mb: float
) -> tuple[bytes, str]:
    if not filename_hint:
        raise InvalidInputError(
            "'filename_hint' is required with 'inline_content' so the format can "
            "be determined (e.g. 'sales.csv' or 'report.xlsx')."
        )
    extension = _extension_of(filename_hint)

    compact = "".join(inline_content.split())  # tolerate line-wrapped base64
    # Base64 inflates size by 4/3; reject oversized input before decoding it.
    _check_size(len(compact) * 3 // 4, max_file_size_mb)
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputError(
            "'inline_content' is not valid base64. Encode the raw file bytes "
            "with standard base64."
        ) from exc
    return raw, extension


def _decode_text(raw: bytes) -> str:
    for encoding in _CSV_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DataParseError("Could not decode the CSV text.")  # pragma: no cover


def _parse_csv(raw: bytes, max_rows: int) -> pd.DataFrame:
    text = _decode_text(raw)
    sample = text[:_SEPARATOR_SNIFF_CHARS]
    separator = ";" if sample.count(";") > sample.count(",") else ","
    try:
        # Read one row past the limit so "too many rows" is detectable
        # without loading an arbitrarily large file into memory.
        return pd.read_csv(io.StringIO(text), sep=separator, nrows=max_rows + 1)
    except pd.errors.EmptyDataError as exc:
        raise DataParseError("The CSV contains no data.") from exc
    except pd.errors.ParserError as exc:
        raise DataParseError(f"The CSV could not be parsed: {exc}") from exc


def _parse_xlsx(raw: bytes, max_rows: int) -> pd.DataFrame:
    try:
        return pd.read_excel(
            io.BytesIO(raw), sheet_name=0, nrows=max_rows + 1, engine="openpyxl"
        )
    except Exception as exc:  # openpyxl raises many types for corrupt files
        raise DataParseError(
            f"The XLSX file could not be read ({type(exc).__name__}). Make sure it "
            "is a valid .xlsx workbook (legacy .xls is not supported)."
        ) from exc
