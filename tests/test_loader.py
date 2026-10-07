import base64
from pathlib import Path

import pandas as pd
import pytest

from data_quality_mcp.profiler_core.exceptions import (
    DataFileNotFoundError,
    DataParseError,
    FileTooLargeError,
    InvalidInputError,
    TooManyRowsError,
    UnsupportedFormatError,
)
from data_quality_mcp.profiler_core.loader import load_data


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- input validation ---


def test_neither_input_raises():
    with pytest.raises(InvalidInputError, match="neither"):
        load_data()


def test_both_inputs_raise(messy_path: Path):
    with pytest.raises(InvalidInputError, match="both"):
        load_data(file_path=str(messy_path), inline_content=_b64(b"a\n1\n"))


# --- file_path mode ---


def test_load_csv_from_path(messy_path: Path):
    df = load_data(file_path=str(messy_path))
    assert df.shape == (520, 10)


def test_missing_file_message_mentions_absolute_path(tmp_path: Path):
    with pytest.raises(DataFileNotFoundError, match="absolute path"):
        load_data(file_path=str(tmp_path / "nope.csv"))


def test_directory_is_not_a_file(tmp_path: Path):
    with pytest.raises(DataFileNotFoundError):
        load_data(file_path=str(tmp_path))


def test_unsupported_extension(tmp_path: Path):
    path = tmp_path / "data.json"
    path.write_text("{}")
    with pytest.raises(UnsupportedFormatError, match=r"\.json"):
        load_data(file_path=str(path))


def test_extension_is_case_insensitive(tmp_path: Path):
    path = tmp_path / "DATA.CSV"
    path.write_text("a,b\n1,2\n")
    assert load_data(file_path=str(path)).shape == (1, 2)


def test_load_xlsx_from_path(tmp_path: Path):
    path = tmp_path / "data.xlsx"
    pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).to_excel(path, index=False)
    df = load_data(file_path=str(path))
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 3


def test_corrupt_xlsx_raises_parse_error(tmp_path: Path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"this is not a zip archive")
    with pytest.raises(DataParseError, match="xlsx"):
        load_data(file_path=str(path))


# --- inline_content mode ---


def test_load_inline_csv():
    df = load_data(inline_content=_b64(b"a,b\n1,2\n3,4\n"), filename_hint="x.csv")
    assert df.shape == (2, 2)


def test_inline_tolerates_line_wrapped_base64():
    encoded = _b64(b"a,b\n1,2\n3,4\n")
    wrapped = "\n".join(encoded[i : i + 4] for i in range(0, len(encoded), 4))
    df = load_data(inline_content=wrapped, filename_hint="x.csv")
    assert df.shape == (2, 2)


def test_inline_requires_filename_hint():
    with pytest.raises(InvalidInputError, match="filename_hint"):
        load_data(inline_content=_b64(b"a\n1\n"))


def test_inline_invalid_base64():
    with pytest.raises(InvalidInputError, match="base64"):
        load_data(inline_content="not base64!!", filename_hint="x.csv")


def test_inline_xlsx(tmp_path: Path):
    path = tmp_path / "data.xlsx"
    pd.DataFrame({"a": [1, 2]}).to_excel(path, index=False)
    df = load_data(inline_content=_b64(path.read_bytes()), filename_hint="d.xlsx")
    assert df["a"].tolist() == [1, 2]


# --- CSV parsing details ---


def test_semicolon_separator_detected():
    df = load_data(inline_content=_b64(b"a;b;c\n1;2;3\n"), filename_hint="x.csv")
    assert list(df.columns) == ["a", "b", "c"]


def test_non_utf8_csv_falls_back(tmp_path: Path):
    path = tmp_path / "latin.csv"
    path.write_bytes("name,city\nJosé,Zürich\n".encode("cp1252"))
    df = load_data(file_path=str(path))
    assert df.loc[0, "city"] == "Zürich"


def test_empty_file(tmp_path: Path):
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(DataParseError, match="empty"):
        load_data(file_path=str(path))


def test_whitespace_only_csv():
    with pytest.raises(DataParseError, match="no data"):
        load_data(inline_content=_b64(b"\n\n"), filename_hint="x.csv")


def test_header_only_csv():
    with pytest.raises(DataParseError, match="no data rows"):
        load_data(inline_content=_b64(b"a,b\n"), filename_hint="x.csv")


def test_malformed_csv():
    with pytest.raises(DataParseError, match="could not be parsed"):
        load_data(inline_content=_b64(b"a,b\n1,2\n3,4,5,6\n"), filename_hint="x.csv")


# --- resource limits ---


def test_file_size_limit(messy_path: Path):
    with pytest.raises(FileTooLargeError, match="exceeds"):
        load_data(file_path=str(messy_path), max_file_size_mb=0.001)


def test_inline_size_limit_checked_before_decoding():
    big = _b64(b"a\n" + b"1\n" * 5000)
    with pytest.raises(FileTooLargeError):
        load_data(inline_content=big, filename_hint="x.csv", max_file_size_mb=0.001)


def test_row_limit(messy_path: Path):
    with pytest.raises(TooManyRowsError, match="100"):
        load_data(file_path=str(messy_path), max_rows=100)


def test_row_limit_exactly_at_boundary_is_allowed():
    csv = b"a\n" + b"1\n" * 10
    df = load_data(inline_content=_b64(csv), filename_hint="x.csv", max_rows=10)
    assert len(df) == 10
