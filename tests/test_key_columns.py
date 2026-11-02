"""Identifier columns whose values repeat with different data
(planning/milestone-3.5.md, D3)."""

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from data_quality_mcp.profiler_core.duplicates import (
    analyze_duplicates,
    is_key_name,
    key_columns,
    key_value,
)
from data_quality_mcp.profiler_core.recommendations import generate_recommendations
from data_quality_mcp.profiler_core.report import prepare_frame


@pytest.mark.parametrize(
    "name",
    [
        "id",
        "ID",
        "Key",
        "uuid",
        "GUID",
        "customer_id",
        "Row ID",
        "ORDER_KEY",
        "order-uuid",
        "orderKey",
        "CustomerID",
        "userId",
        "item2Id",
        " id ",
    ],
)
def test_key_names(name):
    assert is_key_name(name)


@pytest.mark.parametrize(
    "name", ["paid", "valid", "ORDERID", "identity", "keyboard", "idx", "Idaho"]
)
def test_names_that_are_not_keys(name):
    assert not is_key_name(name)


def test_fixture_finds_the_conflicting_id(messy_df: pd.DataFrame):
    result = analyze_duplicates(prepare_frame(messy_df, "file_path"))["key_columns"]
    assert result == [
        {
            "column": "id",
            "conflicting_values": 1,
            "conflicting_rows": 2,
            "sample_values": [484],
            "sample_indices": [13, 65],
            "indices_truncated": False,
        }
    ]


def test_a_clean_key_is_listed_with_zeros(clean_path):
    df = prepare_frame(pd.read_csv(clean_path), "file_path")
    assert analyze_duplicates(df)["key_columns"] == [
        {
            "column": "id",
            "conflicting_values": 0,
            "conflicting_rows": 0,
            "sample_values": [],
            "sample_indices": [],
            "indices_truncated": False,
        }
    ]


def test_a_foreign_key_that_repeats_by_design_is_not_a_key():
    orders = pd.DataFrame(
        {"order_id": range(100), "customer_id": [i % 20 for i in range(100)]}
    )
    assert [k["column"] for k in key_columns(orders)] == ["order_id"]


def test_exact_duplicates_are_not_conflicts():
    # A and A' are exact copies; B shares their key with different data:
    # 1 value on 2 rows (A and B), A' already counted in exact_count
    df = pd.DataFrame(
        {"id": [1, 1, 2, 1] + list(range(3, 40)), "x": [5, 5, 6, 9] + [0] * 37}
    )
    (result,) = key_columns(df)
    assert result["conflicting_values"] == 1
    assert result["conflicting_rows"] == 2
    assert result["sample_indices"] == [0, 3]


def test_null_keys_are_ignored():
    df = pd.DataFrame({"id": [None, None, None] + list(range(40)), "x": range(43)})
    assert key_columns(df)[0]["conflicting_values"] == 0


def test_no_key_like_column_gives_an_empty_list():
    assert (
        analyze_duplicates(pd.DataFrame({"a": range(10), "b": range(10)}))[
            "key_columns"
        ]
        == []
    )


def test_sample_indices_are_capped():
    df = pd.DataFrame(
        {"id": list(range(30)) * 2 + list(range(30, 2000)), "x": range(2030)}
    )
    (result,) = key_columns(df, max_indices=50)
    assert result["conflicting_rows"] == 60
    assert len(result["sample_indices"]) == 50
    assert result["indices_truncated"] is True


def test_the_recommendation_names_the_value_and_the_row_positions(
    messy_df: pd.DataFrame,
):
    # Manual testing: a model read "rows 13, 65" as the repeated IDs
    from data_quality_mcp.profiler_core.report import generate_report

    (message,) = [
        r["message"]
        for r in generate_report(messy_df, "m.csv", "file_path")["recommendations"]
        if r["column"] == "id"
    ]
    assert message == (
        "'id' looks like a row identifier, but its value 484 appears on 2 rows "
        "with different data (0-based row positions 13, 65): compare those rows "
        "and decide which record is correct before deduplicating."
    )


# --- sample_values (planning/milestone-3.5.md, Amendment) ---


def _with_repeats(ids: list, n_unique: int = 60) -> pd.DataFrame:
    """`ids` first, then enough distinct keys to pass the uniqueness gate;
    every row has different data."""
    keys = list(ids) + [f"u{i}" for i in range(n_unique)]
    return pd.DataFrame({"id": keys, "x": range(len(keys))})


def test_float_ids_with_gaps_are_shown_as_integers():
    df = pd.DataFrame(
        {"id": [484.0, None, 484.0] + [float(i) for i in range(40)], "x": range(43)}
    )
    (result,) = key_columns(df)
    assert result["sample_values"] == [484]
    assert type(result["sample_values"][0]) is int


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        (["A-17", "A-17"], ["A-17"]),
        (["007", "007"], ["007"]),
        ([2.5, 2.5], [2.5]),
        ([1e20, 1e20], [1e20]),
        ([9007199254740993, 9007199254740993], [9007199254740993]),
    ],
)
def test_sample_values_keep_their_form(ids, expected):
    rest = [f"u{i}" for i in range(60)] if isinstance(ids[0], str) else list(range(60))
    keys = ids + rest
    (result,) = key_columns(pd.DataFrame({"id": keys, "x": range(len(keys))}))
    assert result["sample_values"] == expected
    assert type(result["sample_values"][0]) is type(expected[0])


def test_infinite_ids_are_spelled_out():
    keys = [float("inf"), float("inf"), -float("inf"), -float("inf")] + [
        float(i) for i in range(60)
    ]
    (result,) = key_columns(pd.DataFrame({"id": keys, "x": range(len(keys))}))
    assert result["conflicting_values"] == 2
    assert result["sample_values"] == ["inf", "-inf"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (np.True_, "true"),
        (np.False_, "false"),
        (np.int64(484), 484),
        (np.float64(484.0), 484),
        (np.float32(2.5), 2.5),
        (float(2**53), float(2**53)),
        (pd.Timestamp("2024-01-01"), "2024-01-01T00:00:00"),
        (pd.Timestamp("2024-01-01", tz="UTC"), "2024-01-01T00:00:00+00:00"),
        (Decimal("1.5"), "1.5"),
    ],
)
def test_key_value(value, expected):
    result = key_value(value)
    assert result == expected
    assert type(result) is type(expected)


def test_a_timestamp_key_gives_its_iso_string():
    stamps = pd.to_datetime(["2024-01-01"] * 2).append(
        pd.date_range("2024-02-01", periods=60, freq="D")
    )
    df = pd.DataFrame({"event_id": stamps, "x": range(len(stamps))})
    (result,) = key_columns(df)
    assert result["sample_values"] == ["2024-01-01T00:00:00"]


def test_values_equal_to_pandas_show_the_first_rows_form():
    # 1, 1.0 and True count as one value in an object column
    keys = [1, 1.0, True] + [f"u{i}" for i in range(60)]
    (result,) = key_columns(pd.DataFrame({"id": keys, "x": range(len(keys))}))
    assert result["conflicting_values"] == 1
    assert result["conflicting_rows"] == 3
    assert result["sample_values"] == [1]


def test_sample_values_follow_the_first_row_not_the_value():
    (result,) = key_columns(_with_repeats(["z", "a", "m", "a", "z", "m"]))
    assert result["sample_values"] == ["z", "a", "m"]
    assert result["sample_indices"] == [0, 1, 2, 3, 4, 5]


def test_sample_values_are_capped_and_the_count_stays_exact():
    ids = [f"k{i}" for i in range(12)] * 2
    (result,) = key_columns(_with_repeats(ids, n_unique=600))
    assert result["conflicting_values"] == 12
    assert result["sample_values"] == [f"k{i}" for i in range(10)]


def test_a_clean_key_has_no_sample_values():
    (result,) = key_columns(_with_repeats([]))
    assert result["conflicting_values"] == 0
    assert result["sample_values"] == []


def _message(key: dict) -> str:
    report = {
        "columns": {},
        "duplicates": {"exact_count": 0, "exact_pct": 0.0, "key_columns": [key]},
        "correlations": {"high_correlation_pairs": []},
    }
    (rec,) = generate_recommendations(report)
    return rec["message"]


def test_several_values_are_named_with_their_row_positions():
    ids = ["1021", "77", "12", "1021", "77", "12", "77"]
    (key,) = key_columns(_with_repeats(ids, n_unique=100))
    assert _message(key) == (
        "'id' looks like a row identifier, but 3 of its values "
        '("1021", "77", "12") each appear on more than one row with different '
        "data (7 rows; 0-based row positions 0, 1, 2, 3, 4 ...): compare those "
        "rows and decide which record is correct before deduplicating."
    )


def test_numbers_print_exactly_and_the_value_list_is_capped_at_five():
    ids = [10**16 + 1 + i for i in range(6)] * 2
    keys = ids + list(range(200))
    (key,) = key_columns(pd.DataFrame({"id": keys, "x": range(len(keys))}))
    message = _message(key)
    assert "6 of its values (10000000000000001, 10000000000000002, " in message
    assert "10000000000000005 ...)" in message
    assert "10000000000000006" not in message


def test_a_large_integer_id_keeps_every_digit_in_the_message():
    keys = [9007199254740993] * 2 + list(range(60))
    (key,) = key_columns(pd.DataFrame({"id": keys, "x": range(len(keys))}))
    assert "its value 9007199254740993 appears on 2 rows" in _message(key)


def test_floats_print_as_in_the_field():
    # A whole float at or above 2**53 stays a float in sample_values, and the
    # message must not print it as an exact integer either
    keys = [1e20, 1e20, 9007199254740993.0, 9007199254740993.0, 2.5, 2.5]
    keys += [float(i) for i in range(100)]
    (key,) = key_columns(pd.DataFrame({"id": keys, "x": range(len(keys))}))
    assert key["sample_values"] == [1e20, 9007199254740992.0, 2.5]
    assert "(1e+20, 9007199254740992.0, 2.5)" in _message(key)


def test_strings_are_escaped_in_the_message():
    (key,) = key_columns(_with_repeats(['say "hi"', 'say "hi"']))
    assert 'its value "say \\"hi\\"" appears' in _message(key)


def test_infinity_is_quoted_in_the_message():
    keys = [float("inf")] * 2 + [float(i) for i in range(60)]
    (key,) = key_columns(pd.DataFrame({"id": keys, "x": range(len(keys))}))
    assert 'its value "inf" appears on 2 rows' in _message(key)


def test_a_lower_value_cap_still_marks_the_list_as_cut():
    (key,) = key_columns(_with_repeats(["a", "b", "c"] * 2), max_sample_values=2)
    assert '3 of its values ("a", "b" ...) each appear' in _message(key)


def test_a_report_without_sample_values_still_gets_a_message():
    message = _message(
        {
            "column": "id",
            "conflicting_values": 1,
            "conflicting_rows": 2,
            "sample_indices": [13, 65],
            "indices_truncated": False,
        }
    )
    assert "one of its values appears on 2 rows with different data" in message
