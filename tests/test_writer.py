"""cleaner_core.writer: serialisation, output paths, and the no-overwrite and
no-partial-file guarantees (planning/milestone-3.md, D4)."""

import base64
import io
import os
from pathlib import Path

import pandas as pd
import pytest

from data_quality_mcp.cleaner_core import writer
from data_quality_mcp.cleaner_core.writer import (
    check_output_path,
    default_output_paths,
    inline_content,
    serialize,
    write_new_file,
    write_output,
)
from data_quality_mcp.profiler_core.exceptions import (
    InvalidInputError,
    OutputExistsError,
    OutputWriteError,
    UnsupportedFormatError,
)
from data_quality_mcp.profiler_core.loader import parse_bytes


@pytest.fixture
def frame():
    return pd.DataFrame({"a": [1, 2], "b": ["x", "é"]})


# --- serialisation ---


def test_csv_has_no_index_no_bom_and_lf_endings(frame):
    raw = serialize(frame, ".csv")
    assert raw == "a,b\n1,x\n2,é\n".encode()


def test_csv_round_trips_through_the_loader(frame):
    back = parse_bytes(serialize(frame, ".csv"), ".csv")
    pd.testing.assert_frame_equal(back, frame, check_dtype=False)


def test_xlsx_round_trips_through_the_loader(frame):
    back = parse_bytes(serialize(frame, ".xlsx"), ".xlsx")
    assert back["a"].tolist() == [1, 2]
    assert back["b"].tolist() == ["x", "é"]


def test_xlsx_stores_timezone_aware_datetimes_as_naive_utc():
    df = pd.DataFrame(
        {"t": pd.to_datetime(["2024-01-01 12:00"]).tz_localize("Europe/Berlin")}
    )
    back = parse_bytes(serialize(df, ".xlsx"), ".xlsx")
    assert back["t"].iloc[0] == pd.Timestamp("2024-01-01 11:00")
    assert back["t"].dt.tz is None


def test_serialisation_errors_become_output_write_errors(monkeypatch, frame):
    def broken(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(pd.DataFrame, "to_excel", broken)
    with pytest.raises(OutputWriteError, match="couldn't be written as .xlsx"):
        serialize(frame, ".xlsx")


# --- inline output ---


def test_inline_csv_is_text_and_xlsx_is_base64(frame):
    assert inline_content(serialize(frame, ".csv"), ".csv", max_mb=1).startswith("a,b")
    raw = serialize(frame, ".xlsx")
    assert base64.b64decode(inline_content(raw, ".xlsx", max_mb=1)) == raw


def test_inline_output_cap_suggests_file_path():
    big = pd.DataFrame({"a": ["x" * 100] * 2000})
    with pytest.raises(InvalidInputError, match="file_path"):
        inline_content(serialize(big, ".csv"), ".csv", max_mb=0.1)


# --- explicit output paths ---


def test_valid_output_path(tmp_path):
    path, extension = check_output_path(str(tmp_path / "Out.XLSX"))
    assert (path.name, extension) == ("Out.XLSX", ".xlsx")


def test_existing_output_path_is_refused(tmp_path):
    existing = tmp_path / "out.csv"
    existing.write_text("keep me")
    with pytest.raises(OutputExistsError, match="never overwrites"):
        check_output_path(str(existing))
    assert existing.read_text() == "keep me"


@pytest.mark.parametrize("name", ["out.json", "out", "out.csv.txt"])
def test_output_path_needs_csv_or_xlsx(tmp_path, name):
    with pytest.raises(UnsupportedFormatError):
        check_output_path(str(tmp_path / name))


def test_relative_output_path_is_refused():
    with pytest.raises(InvalidInputError, match="absolute"):
        check_output_path("out.csv")


@pytest.mark.skipif(os.name != "nt", reason="drive-relative paths exist on Windows")
def test_drive_relative_output_path_is_refused():
    with pytest.raises(InvalidInputError, match="absolute"):
        check_output_path("C:out.csv")


def test_missing_output_folder_is_refused(tmp_path):
    with pytest.raises(InvalidInputError, match="doesn't exist"):
        check_output_path(str(tmp_path / "missing" / "out.csv"))


def test_network_output_path_checked_before_any_filesystem_call(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem touched before the network-path check")

    for attr in ("is_dir", "exists", "stat", "resolve"):
        monkeypatch.setattr(Path, attr, forbidden)
    real_check = writer.check_local_path
    monkeypatch.setattr(
        writer,
        "check_local_path",
        lambda raw, **kw: real_check(raw, windows=True, **kw),
    )
    with pytest.raises(InvalidInputError, match="network or device path"):
        check_output_path("//attacker/share/out.csv")


# --- writing ---


def test_default_names_follow_the_input(tmp_path):
    candidates = default_output_paths(tmp_path / "Sales.CSV", ".csv")
    names = [next(candidates).name for _ in range(3)]
    assert names == ["Sales_cleaned.csv", "Sales_cleaned_2.csv", "Sales_cleaned_3.csv"]


def test_taken_names_are_skipped(tmp_path):
    source = tmp_path / "data.csv"
    (tmp_path / "data_cleaned.csv").write_text("older run")
    written = write_output(b"new", target=None, input_path=source, extension=".csv")
    assert written.name == "data_cleaned_2.csv"
    assert (tmp_path / "data_cleaned.csv").read_text() == "older run"


def test_a_race_for_the_default_name_moves_to_the_next(tmp_path, monkeypatch):
    # Another writer creates the first name between our checks: the "xb" open
    # fails with FileExistsError and the loop takes the next name.
    real_open = open
    first = tmp_path / "data_cleaned.csv"

    def racing_open(path, mode="r", *args, **kwargs):
        if Path(path) == first and not first.exists():
            first.write_bytes(b"other writer")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", racing_open)
    written = write_output(
        b"ours", target=None, input_path=tmp_path / "data.csv", extension=".csv"
    )
    assert written.name == "data_cleaned_2.csv"
    assert first.read_bytes() == b"other writer"


def test_explicit_target_taken_at_write_time(tmp_path):
    target = tmp_path / "out.csv"
    target.write_text("someone else")
    with pytest.raises(OutputExistsError):
        write_output(
            b"x", target=target, input_path=tmp_path / "in.csv", extension=".csv"
        )
    assert target.read_text() == "someone else"


def test_failed_write_leaves_no_partial_file(tmp_path, monkeypatch):
    class FailingFile(io.BytesIO):
        def write(self, data):
            raise OSError(28, "No space left on device")

    real_open = open

    def open_then_fail(path, mode="r", *args, **kwargs):
        real_open(path, mode, *args, **kwargs).close()  # the file now exists
        return FailingFile()

    monkeypatch.setattr("builtins.open", open_then_fail)
    target = tmp_path / "out.csv"
    with pytest.raises(OutputWriteError, match="no partial file"):
        write_new_file(b"data", target)
    assert not target.exists()


def test_unwritable_folder_suggests_output_path(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", denied)
    with pytest.raises(OutputWriteError, match="output_path"):
        write_new_file(b"data", tmp_path / "out.csv")


def test_csv_to_xlsx_conversion_end_to_end(messy_copy):
    from data_quality_mcp.cleaner_core import run_cleaning

    original = messy_copy.read_bytes()
    target = messy_copy.with_name("converted.xlsx")
    result = run_cleaning(
        [{"action": "remove_duplicates"}],
        file_path=str(messy_copy),
        output_path=str(target),
    )
    assert result["output"]["path"] == str(target)
    assert pd.read_excel(target).shape == (501, 10)
    assert messy_copy.read_bytes() == original
