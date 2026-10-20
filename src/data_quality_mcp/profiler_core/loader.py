"""Load a tabular dataset from a file path or from inline content.

Both input modes exist from Milestone 1 so tool signatures don't need to change
later: `file_path` suits local stdio use, `inline_content` suits remote use
where the server cannot see the caller's filesystem. Inline CSV is plain text;
inline XLSX is base64, because it is binary (planning/milestone-3.md, D2).
"""

import base64
import binascii
import io
import re
import zipfile
from pathlib import Path, PureWindowsPath
from typing import NamedTuple

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
from data_quality_mcp.profiler_core.paths import check_local_path

SUPPORTED_EXTENSIONS = (".csv", ".xlsx")

# Encodings tried in order for CSV. latin-1 maps every byte, so it never fails
# and acts as the last resort.
_CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
_SEPARATOR_SNIFF_CHARS = 4096
_MB = 1024 * 1024
_BASE64_TEXT = re.compile(r"[A-Za-z0-9+/]+={0,2}")


class LoadedInput(NamedTuple):
    """A loaded dataset plus what the report says about where it came from."""

    df: pd.DataFrame
    name: str
    source: str  # "file_path" or "inline_content"
    extension: str  # ".csv" or ".xlsx"


def load_input(
    file_path: str | None = None,
    inline_content: str | None = None,
    filename_hint: str | None = None,
    *,
    max_file_size_mb: float = config.MAX_FILE_SIZE_MB,
    max_rows: int = config.MAX_ROWS,
    max_xlsx_uncompressed_mb: float = config.MAX_XLSX_UNCOMPRESSED_MB,
    max_xlsx_entries: int = config.MAX_XLSX_ENTRIES,
    allow_network_paths: bool = config.ALLOW_NETWORK_PATHS,
) -> LoadedInput:
    """`load_data`, plus the dataset's name, source and format.

    The name is the file's name, or the basename of `filename_hint` for
    inline content.
    """
    df = load_data(
        file_path,
        inline_content,
        filename_hint,
        max_file_size_mb=max_file_size_mb,
        max_rows=max_rows,
        max_xlsx_uncompressed_mb=max_xlsx_uncompressed_mb,
        max_xlsx_entries=max_xlsx_entries,
        allow_network_paths=allow_network_paths,
    )
    if inline_content is not None:
        name = _basename(str(filename_hint))
        return LoadedInput(df, name, "inline_content", _extension_of(name))
    name = Path(str(file_path)).name
    return LoadedInput(df, name, "file_path", _extension_of(name))


def load_data(
    file_path: str | None = None,
    inline_content: str | None = None,
    filename_hint: str | None = None,
    *,
    max_file_size_mb: float = config.MAX_FILE_SIZE_MB,
    max_rows: int = config.MAX_ROWS,
    max_xlsx_uncompressed_mb: float = config.MAX_XLSX_UNCOMPRESSED_MB,
    max_xlsx_entries: int = config.MAX_XLSX_ENTRIES,
    allow_network_paths: bool = config.ALLOW_NETWORK_PATHS,
) -> pd.DataFrame:
    """Load a CSV or XLSX dataset into a DataFrame.

    Exactly one of `file_path` or `inline_content` must be given.

    Args:
        file_path: Path to a .csv or .xlsx file on the local filesystem.
        inline_content: The file's content: CSV as plain text, XLSX as base64.
        filename_hint: Filename that sets the format of `inline_content`
            (e.g. "sales.csv"). Required with `inline_content`.
        max_file_size_mb: Reject inputs larger than this.
        max_rows: Reject datasets with more data rows than this.
        max_xlsx_uncompressed_mb, max_xlsx_entries: Reject XLSX files whose
            zip expands to more than this, or has more entries.
        allow_network_paths: Accept UNC and device paths on Windows.

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
        raw, extension = _read_file(
            str(file_path), max_file_size_mb, allow_network_paths
        )
    return parse_bytes(
        raw,
        extension,
        max_rows=max_rows,
        max_xlsx_uncompressed_mb=max_xlsx_uncompressed_mb,
        max_xlsx_entries=max_xlsx_entries,
    )


def parse_bytes(
    raw: bytes,
    extension: str,
    *,
    max_rows: int = config.MAX_ROWS,
    max_xlsx_uncompressed_mb: float = config.MAX_XLSX_UNCOMPRESSED_MB,
    max_xlsx_entries: int = config.MAX_XLSX_ENTRIES,
) -> pd.DataFrame:
    """Parse the raw bytes of a .csv or .xlsx file, enforcing the row limit."""
    if not raw:
        raise DataParseError("The input is empty (0 bytes).")

    if extension == ".csv":
        df = _parse_csv(raw, max_rows)
    else:
        df = _parse_xlsx(raw, max_rows, max_xlsx_uncompressed_mb, max_xlsx_entries)

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


def _basename(filename: str) -> str:
    # PureWindowsPath splits on both / and \, whatever OS the server runs on
    return PureWindowsPath(filename).name


def _extension_of(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported file type '{extension or '(none)'}' for '{filename}'. "
            f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}."
        )
    return extension


def _check_size(size_bytes: int, max_file_size_mb: float) -> None:
    if size_bytes > max_file_size_mb * _MB:
        raise FileTooLargeError(
            f"The input is {size_bytes / _MB:.1f} MB, which exceeds the "
            f"server's limit of {max_file_size_mb:g} MB."
        )


def _read_file(
    file_path: str, max_file_size_mb: float, allow_network_paths: bool
) -> tuple[bytes, str]:
    # Before any filesystem call: a stat on a UNC path already connects
    check_local_path(file_path, allow_network_paths=allow_network_paths)
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
    extension = _extension_of(_basename(filename_hint))

    if extension == ".csv":
        try:
            raw = inline_content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InvalidInputError(
                "'inline_content' contains characters that can't be encoded as "
                "UTF-8 text."
            ) from exc
        _check_size(len(raw), max_file_size_mb)
        _reject_base64_csv(inline_content)
        return raw, extension

    compact = "".join(inline_content.split())  # tolerate line-wrapped base64
    # Base64 inflates size by 4/3; reject oversized input before decoding it.
    _check_size(len(compact) * 3 // 4, max_file_size_mb)
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputError(
            "'inline_content' is not valid base64. An .xlsx file must be sent as "
            "its raw bytes, base64-encoded (CSV is sent as plain text)."
        ) from exc
    return raw, extension


def _reject_base64_csv(text: str) -> None:
    """A single-line, valid-base64 payload that decodes to multi-line text is a
    base64-encoded CSV (the pre-Milestone-3 format). Parsing it as CSV would
    give a silent one-column result, so say what to send instead."""
    stripped = text.strip()
    if "\n" in stripped or len(stripped) % 4 or not _BASE64_TEXT.fullmatch(stripped):
        return
    try:
        decoded = base64.b64decode(stripped, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return
    if "\n" in decoded:
        raise InvalidInputError(
            "'inline_content' looks base64-encoded. Send CSV as plain text; "
            "base64 is only for .xlsx files."
        )


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


def _unreadable_xlsx(exc: Exception) -> DataParseError:
    return DataParseError(
        f"The XLSX file could not be read ({type(exc).__name__}). Make sure it "
        "is a valid .xlsx workbook (legacy .xls is not supported)."
    )


def _check_xlsx_expansion(raw: bytes, max_uncompressed_mb: float, max_entries: int):
    """An XLSX is a zip. Python's zipfile (which openpyxl reads through) stops
    decompressing an entry at its declared size and fails the CRC check on a
    mismatch, so the declared sizes bound what is actually decompressed."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise _unreadable_xlsx(exc) from exc
    if len(entries) > max_entries:
        raise FileTooLargeError(
            f"The XLSX file has {len(entries):,} internal parts, more than the "
            f"server's limit of {max_entries:,}. Send the data as CSV instead."
        )
    uncompressed = sum(entry.file_size for entry in entries)
    if uncompressed > max_uncompressed_mb * _MB:
        raise FileTooLargeError(
            f"The XLSX file expands to {uncompressed / _MB:.1f} MB when unpacked, "
            f"which exceeds the server's limit of {max_uncompressed_mb:g} MB. "
            "Send the data as CSV instead."
        )


def _parse_xlsx(
    raw: bytes, max_rows: int, max_uncompressed_mb: float, max_entries: int
) -> pd.DataFrame:
    _check_xlsx_expansion(raw, max_uncompressed_mb, max_entries)
    try:
        return pd.read_excel(
            io.BytesIO(raw), sheet_name=0, nrows=max_rows + 1, engine="openpyxl"
        )
    except Exception as exc:  # openpyxl raises many types for corrupt files
        raise _unreadable_xlsx(exc) from exc
