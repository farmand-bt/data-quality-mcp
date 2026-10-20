"""Serialise cleaned data and write it as a new file (planning/milestone-3.md, D4).

Guarantees: no existing file is ever overwritten (exclusive create, even when
two calls race for a name), and no partial file is left behind (data is
serialised in memory first, and a failed write deletes what it created).
"""

import base64
import io
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.exceptions import (
    InvalidInputError,
    OutputExistsError,
    OutputWriteError,
    UnsupportedFormatError,
)
from data_quality_mcp.profiler_core.paths import check_local_path

OUTPUT_EXTENSIONS = (".csv", ".xlsx")
_MAX_NAME_ATTEMPTS = 1000
_MB = 1024 * 1024


def serialize(df: pd.DataFrame, extension: str) -> bytes:
    """CSV: no index column, UTF-8 without a BOM, "\\n" line endings, pandas'
    default ISO datetime format. XLSX: no index column; timezone-aware
    datetimes become naive UTC, because Excel can't store a timezone."""
    try:
        if extension == ".csv":
            return df.to_csv(index=False, lineterminator="\n").encode("utf-8")
        frame = df.copy()
        for column in frame.columns:
            if isinstance(frame[column].dtype, pd.DatetimeTZDtype):
                frame[column] = frame[column].dt.tz_convert("UTC").dt.tz_localize(None)
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False, engine="openpyxl")
        return buffer.getvalue()
    except Exception as exc:  # pandas/openpyxl raise many types here
        raise OutputWriteError(
            f"The cleaned data couldn't be written as {extension} "
            f"({type(exc).__name__}: {exc}). Try the other format."
        ) from exc


def inline_content(raw: bytes, extension: str, *, max_mb: float) -> str:
    """The output as tool-result content: CSV as text, XLSX as base64, capped
    on the encoded length (it goes straight into the model's context)."""
    content = (
        raw.decode("utf-8")
        if extension == ".csv"
        else base64.b64encode(raw).decode("ascii")
    )
    size = len(content.encode("utf-8"))
    if size > max_mb * _MB:
        raise InvalidInputError(
            f"The cleaned data is {size / _MB:.2f} MB, over the {max_mb:g} MB limit "
            "for inline output (for XLSX the limit counts the base64 text, so "
            "about three quarters of it is file data). Pass the data as "
            "'file_path' instead, so the result is written to a file."
        )
    return content


def check_output_path(
    output_path: str,
    *,
    allow_network_paths: bool = config.ALLOW_NETWORK_PATHS,
) -> tuple[Path, str]:
    """Validate an explicit output path; return it and its extension.

    The network-path check runs on the raw string before any filesystem call.
    """
    check_local_path(output_path, allow_network_paths=allow_network_paths)
    path = Path(output_path)
    if not path.is_absolute():
        raise InvalidInputError(
            f"'output_path' must be an absolute path (got '{output_path}'), e.g. "
            "'C:\\data\\sales_cleaned.csv'."
        )
    extension = path.suffix.lower()
    if extension not in OUTPUT_EXTENSIONS:
        raise UnsupportedFormatError(
            f"'output_path' must end in .csv or .xlsx (got '{path.name}')."
        )
    if not path.parent.is_dir():
        raise InvalidInputError(
            f"The folder for 'output_path' doesn't exist: '{path.parent}'."
        )
    if path.exists():
        raise OutputExistsError(
            f"'{output_path}' already exists, and cleaning never overwrites a "
            "file. Choose a new name, or omit 'output_path' for an automatic one."
        )
    return path, extension


def default_output_paths(
    input_path: Path, extension: str, *, suffix: str = config.CLEANED_SUFFIX
) -> Iterator[Path]:
    """`<stem>_cleaned<ext>` next to the input, then `_cleaned_2`, `_cleaned_3`..."""
    folder, stem = input_path.parent, input_path.stem
    yield folder / f"{stem}{suffix}{extension}"
    for n in range(2, _MAX_NAME_ATTEMPTS + 1):
        yield folder / f"{stem}{suffix}_{n}{extension}"


def write_new_file(raw: bytes, path: Path) -> None:
    """Create `path` exclusively and write `raw`; delete it if the write fails.

    Raises FileExistsError (unchanged) when the name is taken, so the caller
    can try the next one.
    """
    try:
        handle = open(path, "xb")  # noqa: SIM115 - closed below, cleanup on error
    except FileExistsError:
        raise
    except OSError as exc:
        raise OutputWriteError(
            f"Can't create '{path}' ({exc.strerror or exc}). Pass an "
            "'output_path' in a folder you can write to."
        ) from exc
    try:
        with handle:
            handle.write(raw)
    except BaseException as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise OutputWriteError(
                f"Writing '{path}' failed ({exc.strerror or exc}); no partial file "
                "was left. Free some disk space or choose another folder."
            ) from exc
        raise


def write_output(
    raw: bytes,
    *,
    target: Path | None,
    input_path: Path,
    extension: str,
    suffix: str = config.CLEANED_SUFFIX,
) -> Path:
    """Write to `target` if given, else to the first free default name."""
    if target is not None:
        try:
            write_new_file(raw, target)
        except FileExistsError as exc:
            raise OutputExistsError(
                f"'{target}' already exists, and cleaning never overwrites a file."
            ) from exc
        return target
    for candidate in default_output_paths(input_path, extension, suffix=suffix):
        try:
            write_new_file(raw, candidate)
        except FileExistsError:
            continue
        return candidate
    raise OutputWriteError(
        f"No free output name next to '{input_path}' after {_MAX_NAME_ATTEMPTS} "
        "tries. Pass an 'output_path'."
    )
