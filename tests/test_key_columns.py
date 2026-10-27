"""Identifier columns whose values repeat with different data
(planning/milestone-3.5.md, D3)."""

import pandas as pd
import pytest

from data_quality_mcp.profiler_core.duplicates import (
    analyze_duplicates,
    is_key_name,
    key_columns,
)
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
