import base64
import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from data_quality_mcp.profiler_core import loader
from data_quality_mcp.profiler_core.exceptions import (
    DataFileNotFoundError,
    DataParseError,
    FileTooLargeError,
    InvalidInputError,
    TooManyRowsError,
    UnsupportedFormatError,
)
from data_quality_mcp.profiler_core.loader import load_data, load_input
from data_quality_mcp.profiler_core.paths import check_local_path


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _xlsx_bytes(tmp_path: Path, df: pd.DataFrame) -> bytes:
    path = tmp_path / "data.xlsx"
    df.to_excel(path, index=False)
    return path.read_bytes()


# --- input validation ---


def test_neither_input_raises():
    with pytest.raises(InvalidInputError, match="neither"):
        load_data()


def test_both_inputs_raise(messy_path: Path):
    with pytest.raises(InvalidInputError, match="both"):
        load_data(file_path=str(messy_path), inline_content="a\n1\n")


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


# --- inline_content mode: CSV is plain text, XLSX is base64 ---


def test_load_inline_csv_text():
    df = load_data(inline_content="a,b\n1,2\n3,4\n", filename_hint="x.csv")
    assert df.shape == (2, 2)


def test_inline_csv_with_bom_and_crlf():
    df = load_data(inline_content="\ufeffa,b\r\n1,2\r\n3,4\r\n", filename_hint="x.csv")
    assert list(df.columns) == ["a", "b"]
    assert df["b"].tolist() == [2, 4]


def test_inline_csv_non_ascii_text():
    df = load_data(inline_content="name,city\nJosé,Zürich\n", filename_hint="x.csv")
    assert df.loc[0, "city"] == "Zürich"


def test_inline_csv_sent_as_base64_is_rejected():
    with pytest.raises(InvalidInputError, match="plain text"):
        load_data(inline_content=_b64(b"a,b\n1,2\n3,4\n"), filename_hint="x.csv")


def test_single_line_csv_that_happens_to_be_base64_alphabet_still_loads():
    # "abcd" is valid base64 but decodes to bytes without a newline: a header
    with pytest.raises(DataParseError, match="no data rows"):
        load_data(inline_content="abcd", filename_hint="x.csv")


def test_inline_requires_filename_hint():
    with pytest.raises(InvalidInputError, match="filename_hint"):
        load_data(inline_content="a\n1\n")


def test_inline_xlsx_base64(tmp_path: Path):
    raw = _xlsx_bytes(tmp_path, pd.DataFrame({"a": [1, 2]}))
    df = load_data(inline_content=_b64(raw), filename_hint="d.xlsx")
    assert df["a"].tolist() == [1, 2]


def test_inline_xlsx_tolerates_line_wrapped_base64(tmp_path: Path):
    encoded = _b64(_xlsx_bytes(tmp_path, pd.DataFrame({"a": [1, 2]})))
    wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    assert load_data(inline_content=wrapped, filename_hint="d.xlsx").shape == (2, 1)


def test_inline_xlsx_invalid_base64():
    with pytest.raises(InvalidInputError, match="base64"):
        load_data(inline_content="not base64!!", filename_hint="x.xlsx")


@pytest.mark.parametrize(
    "hint, name",
    [
        ("sales.csv", "sales.csv"),
        ("C:\\Users\\me\\sales.csv", "sales.csv"),
        ("/home/me/data/sales.csv", "sales.csv"),
        ("SALES.CSV", "SALES.CSV"),
    ],
)
def test_filename_hint_is_reduced_to_its_basename(hint, name):
    loaded = load_input(inline_content="a\n1\n", filename_hint=hint)
    assert (loaded.name, loaded.source, loaded.extension) == (
        name,
        "inline_content",
        ".csv",
    )


def test_inline_unsupported_extension():
    with pytest.raises(UnsupportedFormatError, match=r"\.txt"):
        load_data(inline_content="a\n1\n", filename_hint="x.txt")


def test_load_input_from_file(messy_path: Path):
    loaded = load_input(file_path=str(messy_path))
    assert (loaded.name, loaded.source, loaded.extension) == (
        "messy_sample.csv",
        "file_path",
        ".csv",
    )
    assert loaded.df.shape == (520, 10)


# --- network and device paths (Windows) ---


@pytest.mark.parametrize(
    "path",
    [
        "\\\\server\\share\\x.csv",
        "//server/share/x.csv",
        "/\\server\\share\\x.csv",
        "\\\\?\\C:\\data\\x.csv",
        "\\\\.\\pipe\\x",
        "\\??\\UNC\\server\\share\\x.csv",
        "/??/UNC/server/share/x.csv",
        "\\??\\C:\\data\\x.csv",
    ],
)
def test_network_and_device_paths_rejected_on_windows(path):
    with pytest.raises(InvalidInputError, match="network or device path"):
        check_local_path(path, windows=True)
    check_local_path(path, windows=True, allow_network_paths=True)
    check_local_path(path, windows=False)  # POSIX: an ordinary path


def test_local_paths_accepted():
    for path in ("C:\\data\\x.csv", "C:/data/x.csv", "/home/me/x.csv", "x.csv"):
        check_local_path(path, windows=True)


def test_network_path_rejected_before_any_filesystem_call(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem touched before the network-path check")

    for attr in ("resolve", "exists", "is_file", "stat", "expanduser"):
        monkeypatch.setattr(Path, attr, forbidden)
    monkeypatch.setattr(
        loader,
        "check_local_path",
        lambda raw, **kw: check_local_path(raw, windows=True, **kw),
    )
    with pytest.raises(InvalidInputError, match="network or device path"):
        load_data(file_path="\\\\attacker\\share\\x.csv")


# --- CSV parsing details ---


def test_semicolon_separator_detected():
    df = load_data(inline_content="a;b;c\n1;2;3\n", filename_hint="x.csv")
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


def test_empty_inline_content():
    with pytest.raises(DataParseError, match="empty"):
        load_data(inline_content="", filename_hint="x.csv")


def test_whitespace_only_csv():
    with pytest.raises(DataParseError, match="no data"):
        load_data(inline_content="\n\n", filename_hint="x.csv")


def test_header_only_csv():
    with pytest.raises(DataParseError, match="no data rows"):
        load_data(inline_content="a,b\n", filename_hint="x.csv")


def test_malformed_csv():
    with pytest.raises(DataParseError, match="could not be parsed"):
        load_data(inline_content="a,b\n1,2\n3,4,5,6\n", filename_hint="x.csv")


# --- resource limits ---


def test_file_size_limit(messy_path: Path):
    with pytest.raises(FileTooLargeError, match="exceeds"):
        load_data(file_path=str(messy_path), max_file_size_mb=0.001)


def test_inline_csv_size_limit_uses_utf8_bytes():
    text = "a\n" + "é\n" * 400  # 2 bytes per é: 802 characters, 1202 bytes
    with pytest.raises(FileTooLargeError):
        load_data(inline_content=text, filename_hint="x.csv", max_file_size_mb=0.001)


def test_inline_xlsx_size_limit_checked_before_decoding():
    big = "A" * 4000  # never decoded: rejected on its encoded length
    with pytest.raises(FileTooLargeError):
        load_data(inline_content=big, filename_hint="x.xlsx", max_file_size_mb=0.001)


def test_row_limit(messy_path: Path):
    with pytest.raises(TooManyRowsError, match="100"):
        load_data(file_path=str(messy_path), max_rows=100)


def test_row_limit_exactly_at_boundary_is_allowed():
    csv = "a\n" + "1\n" * 10
    df = load_data(inline_content=csv, filename_hint="x.csv", max_rows=10)
    assert len(df) == 10


def _zip_with(entries: int, size: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for i in range(entries):
            archive.writestr(f"part{i}.xml", b"0" * size)
    return buffer.getvalue()


def test_xlsx_entry_limit():
    raw = _zip_with(entries=11, size=1)
    with pytest.raises(FileTooLargeError, match="internal parts"):
        load_data(inline_content=_b64(raw), filename_hint="x.xlsx", max_xlsx_entries=10)


def test_xlsx_uncompressed_limit(tmp_path: Path):
    raw = _zip_with(entries=1, size=2 * 1024 * 1024)  # 2 MB of zeros: tiny zipped
    path = tmp_path / "bomb.xlsx"
    path.write_bytes(raw)
    with pytest.raises(FileTooLargeError, match="expands to 2.0 MB"):
        load_data(file_path=str(path), max_xlsx_uncompressed_mb=1)


def test_real_xlsx_passes_the_expansion_checks(tmp_path: Path):
    raw = _xlsx_bytes(tmp_path, pd.DataFrame({"a": range(100)}))
    assert len(load_data(inline_content=_b64(raw), filename_hint="d.xlsx")) == 100
