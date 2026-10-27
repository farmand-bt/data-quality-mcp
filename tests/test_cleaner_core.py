"""cleaner_core: handlers, pipeline and validation, and run_cleaning.

TestAnalyzerParity* classes port llm-data-quality-analyzer's tests/test_cleaner.py
unchanged except where planning/milestone-3.md lists a deviation (unknown
strategies raise the domain InvalidInputError instead of ValueError).
"""

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data_quality_mcp.cleaner_core import run_cleaning
from data_quality_mcp.cleaner_core.duplicate_handler import remove_duplicates
from data_quality_mcp.cleaner_core.missing_handler import handle_missing
from data_quality_mcp.cleaner_core.outlier_handler import handle_outliers
from data_quality_mcp.cleaner_core.pipeline import run_steps, step_priority
from data_quality_mcp.cleaner_core.type_fixer import fix_types
from data_quality_mcp.profiler_core.exceptions import InvalidInputError
from data_quality_mcp.profiler_core.report import generate_report

# --- fixtures (as in the analyzer) ---


@pytest.fixture
def df_with_missing():
    return pd.DataFrame(
        {"x": [1.0, 2.0, None, 4.0, None], "cat": ["a", "b", None, "a", "b"]}
    )


@pytest.fixture
def df_with_dups():
    return pd.DataFrame({"a": [1, 2, 1, 3], "b": ["x", "y", "x", "z"]})


@pytest.fixture
def df_mixed_types():
    return pd.DataFrame(
        {
            "price_str": ["1,200", "$500", "300", "nan", "1500"],
            "date_str": [
                "2020-01-01",
                "2021-06-15",
                "2022-03-20",
                "2023-08-10",
                "2019-11-05",
            ],
            "flag_str": ["yes", "no", "yes", "no", "yes"],
        }
    )


@pytest.fixture
def df_with_outliers():
    return pd.DataFrame({"x": list(range(1, 21)) + [1000]})


# --- analyzer parity: handlers ---


class TestAnalyzerParityHandleMissing:
    def test_drop_rows(self, df_with_missing):
        out, log = handle_missing(df_with_missing, "drop_rows", "x")
        assert out["x"].isnull().sum() == 0
        assert len(out) == 3
        assert log["rows_removed"] == 2

    def test_drop_column(self, df_with_missing):
        out, _ = handle_missing(df_with_missing, "drop_column", "x")
        assert "x" not in out.columns
        assert "cat" in out.columns

    def test_fill_mean(self, df_with_missing):
        out, log = handle_missing(df_with_missing, "fill_mean", "x")
        assert out["x"].isnull().sum() == 0
        assert log["missing_after"] == 0

    def test_fill_median(self, df_with_missing):
        out, _ = handle_missing(df_with_missing, "fill_median", "x")
        assert out["x"].isnull().sum() == 0

    def test_fill_mode(self, df_with_missing):
        out, _ = handle_missing(df_with_missing, "fill_mode", "cat")
        assert out["cat"].isnull().sum() == 0

    def test_fill_constant(self, df_with_missing):
        out, _ = handle_missing(df_with_missing, "fill_constant", "x", value=99.0)
        assert (out["x"] == 99.0).sum() == 2
        assert out["x"].isnull().sum() == 0

    def test_ffill(self, df_with_missing):
        out, _ = handle_missing(df_with_missing, "ffill", "x")
        assert out["x"].iloc[2] == 2.0

    def test_bfill(self, df_with_missing):
        out, _ = handle_missing(df_with_missing, "bfill", "x")
        assert out["x"].iloc[2] == 4.0

    def test_non_destructive(self, df_with_missing):
        original_nulls = df_with_missing["x"].isnull().sum()
        handle_missing(df_with_missing, "fill_mean", "x")
        assert df_with_missing["x"].isnull().sum() == original_nulls

    def test_unknown_strategy_raises(self, df_with_missing):
        with pytest.raises(InvalidInputError, match="Unknown strategy"):
            handle_missing(df_with_missing, "magic_fix", "x")

    def test_log_schema(self, df_with_missing):
        _, log = handle_missing(df_with_missing, "fill_mean", "x")
        for key in (
            "action",
            "column",
            "strategy",
            "rows_before",
            "rows_after",
            "rows_removed",
            "missing_before",
            "missing_after",
        ):
            assert key in log


class TestAnalyzerParityRemoveDuplicates:
    def test_keep_first(self, df_with_dups):
        out, log = remove_duplicates(df_with_dups, keep="first")
        assert len(out) == 3
        assert log["rows_removed"] == 1

    def test_keep_last(self, df_with_dups):
        out, _ = remove_duplicates(df_with_dups, keep="last")
        assert len(out) == 3

    def test_remove_all(self, df_with_dups):
        out, log = remove_duplicates(df_with_dups, keep="none")
        assert len(out) == 2
        assert log["rows_removed"] == 2
        assert log["strategy"] == "remove_all"

    def test_no_duplicates(self):
        out, log = remove_duplicates(pd.DataFrame({"a": [1, 2, 3]}))
        assert len(out) == 3
        assert log["rows_removed"] == 0

    def test_non_destructive(self, df_with_dups):
        remove_duplicates(df_with_dups, keep="first")
        assert len(df_with_dups) == 4

    def test_log_schema(self, df_with_dups):
        _, log = remove_duplicates(df_with_dups)
        for key in (
            "action",
            "column",
            "strategy",
            "rows_before",
            "rows_after",
            "rows_removed",
        ):
            assert key in log


class TestAnalyzerParityFixTypes:
    def test_to_numeric_strips_commas(self, df_mixed_types):
        out, _ = fix_types(df_mixed_types, "price_str", "numeric")
        assert out["price_str"].dtype in (float, np.float64)
        assert out["price_str"].iloc[0] == pytest.approx(1200.0)

    def test_to_numeric_strips_currency(self, df_mixed_types):
        out, _ = fix_types(df_mixed_types, "price_str", "numeric")
        assert out["price_str"].iloc[1] == pytest.approx(500.0)

    def test_to_datetime(self, df_mixed_types):
        out, _ = fix_types(df_mixed_types, "date_str", "datetime")
        assert pd.api.types.is_datetime64_any_dtype(out["date_str"])

    def test_to_categorical(self):
        out, _ = fix_types(
            pd.DataFrame({"cat": ["a", "b", "a", "c"]}), "cat", "categorical"
        )
        assert hasattr(out["cat"], "cat")

    def test_to_string_preserves_nan(self):
        out, _ = fix_types(pd.DataFrame({"x": [1.0, None, 3.0]}), "x", "string")
        assert out["x"].isnull().sum() == 1

    def test_to_boolean(self, df_mixed_types):
        out, _ = fix_types(df_mixed_types, "flag_str", "boolean")
        assert set(out["flag_str"].dropna().unique()).issubset({True, False})

    def test_to_lowercase(self):
        df = pd.DataFrame({"sex": ["Male", "FEMALE", "male", None]})
        out, _ = fix_types(df, "sex", "lowercase")
        assert out["sex"].tolist()[:3] == ["male", "female", "male"]
        assert pd.isna(out["sex"].iloc[3])
        assert hasattr(out["sex"], "cat")

    def test_to_lowercase_non_destructive(self):
        df = pd.DataFrame({"cat": ["Apple", "APPLE"]})
        fix_types(df, "cat", "lowercase")
        assert df["cat"].iloc[0] == "Apple"

    def test_unknown_type_raises(self):
        with pytest.raises(InvalidInputError, match="Unknown target_type"):
            fix_types(pd.DataFrame({"x": [1, 2, 3]}), "x", "imaginary_type")

    def test_non_destructive(self, df_mixed_types):
        original_dtype = df_mixed_types["price_str"].dtype
        fix_types(df_mixed_types, "price_str", "numeric")
        assert df_mixed_types["price_str"].dtype == original_dtype

    def test_log_schema(self, df_mixed_types):
        _, log = fix_types(df_mixed_types, "price_str", "numeric")
        for key in ("action", "column", "strategy", "original_dtype", "new_dtype"):
            assert key in log


class TestAnalyzerParityHandleOutliers:
    def test_remove_strategy(self, df_with_outliers):
        out, log = handle_outliers(df_with_outliers, "x", [20], "remove")
        assert 1000 not in out["x"].values
        assert log["rows_removed"] == 1

    def test_cap_iqr_strategy(self, df_with_outliers):
        out, _ = handle_outliers(df_with_outliers, "x", [20], "cap_iqr")
        assert len(out) == len(df_with_outliers)
        assert out["x"].max() < 1000

    def test_replace_nan_strategy(self, df_with_outliers):
        out, _ = handle_outliers(df_with_outliers, "x", [20], "replace_nan")
        assert len(out) == len(df_with_outliers)
        assert pd.isna(out.loc[20, "x"])

    def test_invalid_indices_ignored(self, df_with_outliers):
        out, log = handle_outliers(df_with_outliers, "x", [999], "remove")
        assert len(out) == len(df_with_outliers)
        assert log["outliers_treated"] == 0

    def test_unknown_strategy_raises(self, df_with_outliers):
        with pytest.raises(InvalidInputError, match="Unknown strategy"):
            handle_outliers(df_with_outliers, "x", [20], "teleport")

    def test_non_destructive(self, df_with_outliers):
        handle_outliers(df_with_outliers, "x", [20], "cap_iqr")
        assert df_with_outliers["x"].max() == 1000

    def test_log_schema(self, df_with_outliers):
        _, log = handle_outliers(df_with_outliers, "x", [20], "remove")
        for key in (
            "action",
            "column",
            "strategy",
            "outliers_treated",
            "rows_before",
            "rows_after",
            "rows_removed",
        ):
            assert key in log

    def test_cap_iqr_ignores_inf_for_fences_and_caps_it(self):
        df = pd.DataFrame({"x": [float(i) for i in range(1, 21)] + [np.inf, -np.inf]})
        out, log = handle_outliers(df, "x", [20, 21], "cap_iqr")
        # Fences come from 1..20 (Q1 = 5.75, Q3 = 15.25, IQR = 9.5): [-8.5, 29.5]
        assert out["x"].iloc[20] == pytest.approx(29.5)
        assert out["x"].iloc[21] == pytest.approx(-8.5)
        assert out["x"].iloc[:20].tolist() == df["x"].iloc[:20].tolist()
        assert log["outliers_treated"] == 2  # deviation: values actually clipped


class TestAnalyzerParityStepPriority:
    """Priority 0 = no row change, 1 = outlier remove, 2 = row/column drops."""

    @pytest.mark.parametrize(
        ("step", "priority"),
        [
            ({"action": "fix_type", "column": "x", "target_type": "numeric"}, 0),
            ({"action": "handle_missing", "column": "x", "strategy": "fill_mean"}, 0),
            (
                {
                    "action": "handle_missing",
                    "column": "x",
                    "strategy": "fill_constant",
                },
                0,
            ),
            ({"action": "handle_outliers", "column": "x", "strategy": "cap_iqr"}, 0),
            (
                {"action": "handle_outliers", "column": "x", "strategy": "replace_nan"},
                0,
            ),
            ({"action": "handle_outliers", "column": "x", "strategy": "remove"}, 1),
            ({"action": "handle_missing", "column": "x", "strategy": "drop_rows"}, 2),
            ({"action": "handle_missing", "column": "x", "strategy": "drop_column"}, 2),
            ({"action": "remove_duplicates"}, 2),
        ],
    )
    def test_priority(self, step, priority):
        assert step_priority(step) == priority


class TestAnalyzerParityPipeline:
    def test_execute_applies_steps(self, df_with_missing):
        steps = [{"action": "handle_missing", "column": "x", "strategy": "fill_mean"}]
        out, log = run_steps(df_with_missing, steps)
        assert out["x"].isnull().sum() == 0
        assert log[0]["action"] == "handle_missing"

    def test_execute_does_not_modify_original(self, df_with_missing):
        steps = [{"action": "handle_missing", "column": "x", "strategy": "fill_mean"}]
        run_steps(df_with_missing, steps)
        assert df_with_missing["x"].isnull().sum() == 2

    def test_multi_step_pipeline(self, df_with_missing):
        combined = pd.concat([df_with_missing, df_with_missing], ignore_index=True)
        steps = [
            {"action": "handle_missing", "column": "x", "strategy": "fill_mean"},
            {"action": "remove_duplicates", "keep": "first"},
        ]
        out, _ = run_steps(combined, steps)
        assert out["x"].isnull().sum() == 0
        assert out.duplicated().sum() == 0

    def test_fix_type_step(self, df_mixed_types):
        steps = [
            {"action": "fix_type", "column": "price_str", "target_type": "numeric"}
        ]
        out, _ = run_steps(df_mixed_types, steps)
        assert pd.api.types.is_numeric_dtype(out["price_str"])

    def test_outlier_step(self, df_with_outliers):
        steps = [{"action": "handle_outliers", "column": "x", "strategy": "remove"}]
        out, _ = run_steps(df_with_outliers, steps)
        assert 1000 not in out["x"].values


# --- timestamps with mixed UTC offsets ---

TWO_OFFSETS = ["2020-01-01T10:00:00Z", "2020-01-02T10:00:00+02:00"]


def test_to_datetime_with_mixed_offsets_converts_to_utc():
    # Deviation: the analyzer raises "Mixed timezones detected" here
    values = ["2020-01-01T10:00:00Z", "2020-01-02T10:00:00+02:00", None]
    df = pd.DataFrame({"t": values})
    out, _ = fix_types(df, "t", "datetime")
    assert str(out["t"].dt.tz) == "UTC"
    assert out["t"].iloc[1] == pd.Timestamp("2020-01-02T08:00:00Z")  # same instant
    assert out["t"].isna().iloc[2]


@pytest.mark.parametrize(
    "other",
    [
        "2020-01-02 10:00",  # naive timestamp
        "15-Jan-2020",  # naive date whose "-2020" looks like an offset
        "01-15-2020",
    ],
)
def test_to_datetime_mixing_naive_and_offset_values_is_rejected(other):
    df = pd.DataFrame({"t": [*TWO_OFFSETS, other]})
    with pytest.raises(InvalidInputError, match="with and without a timezone"):
        fix_types(df, "t", "datetime")


@pytest.mark.parametrize(
    "other",
    [
        "2020-01-03 10:00:00+02",  # hours-only offset
        "2020-01-03 10:00 UTC",
        "2020-01-03T10:00:00z",  # lowercase z
        "2020-01-03 10:00 -0500",
    ],
)
def test_to_datetime_recognises_offset_forms(other):
    df = pd.DataFrame({"t": [*TWO_OFFSETS, other]})
    out, _ = fix_types(df, "t", "datetime")
    assert str(out["t"].dt.tz) == "UTC"
    assert out["t"].notna().all()


def test_to_datetime_with_mixed_offsets_nulls_junk_values():
    # A value that isn't a timestamp has no timezone to compare, so it
    # doesn't block the conversion: it becomes null, as for any conversion
    values = ["2020-01-01T10:00:00Z", "2020-01-02T10:00:00+02:00", "unknown", "TBD"]
    out, log = run_steps(
        pd.DataFrame({"t": values}),
        [{"action": "fix_type", "column": "t", "target_type": "datetime"}],
    )
    assert out["t"].isna().tolist() == [False, False, True, True]
    assert log[0]["values_nulled"] == 2


def test_clean_dataset_converts_mixed_offsets(tmp_path: Path):
    path = tmp_path / "t.csv"
    path.write_text(
        "t,x\n2020-01-01T10:00:00Z,1\n2020-01-02T10:00:00+02:00,2\n", encoding="utf-8"
    )
    steps = [{"action": "fix_type", "column": "t", "target_type": "datetime"}]
    result = run_cleaning(steps, file_path=str(path))
    written = Path(result["output"]["path"]).read_text(encoding="utf-8")
    assert "2020-01-02 08:00:00+00:00" in written


# --- bug fixes (ported back to the analyzer after M3) ---


def test_two_outlier_removals_delete_the_right_rows():
    # Analyzer: removing a's outlier resets the index, so b's index 5 then
    # points at the row that held b=15, and b=999 survives.
    df = pd.DataFrame(
        {
            "a": [1, 2, 3, 4, 100, 5, 6, 3, 2, 4],
            "b": [10, 11, 12, 13, 14, 999, 15, 12, 11, 13],
        }
    )
    steps = [
        {"action": "handle_outliers", "column": "a", "strategy": "remove"},
        {"action": "handle_outliers", "column": "b", "strategy": "remove"},
    ]
    out, log = run_steps(df, steps)
    assert 100 not in out["a"].values and 999 not in out["b"].values
    assert 15 in out["b"].values
    assert [e["outliers_treated"] for e in log] == [1, 1]
    assert list(out.index) == list(range(len(out)))  # reset once, at the end


def test_fill_mean_on_a_column_without_numbers_is_rejected():
    # Analyzer: ValueError: cannot convert float NaN to integer
    for column in (
        pd.Series([None, None, None], dtype=object),
        pd.Series(["x", None, "y"]),
    ):
        steps = [{"action": "handle_missing", "column": "c", "strategy": "fill_mean"}]
        with pytest.raises(InvalidInputError, match="numeric column"):
            run_steps(pd.DataFrame({"c": column}), steps)


def test_before_and_after_duplicates_use_one_definition(messy_copy):
    # Analyzer UI: before = rows dropped keeping the first copy, after =
    # every row in a group. Here both are exact_count.
    steps = [{"action": "handle_missing", "column": "price", "strategy": "fill_median"}]
    result = run_cleaning(steps, file_path=str(messy_copy))
    assert result["before"]["exact_duplicates"] == 19
    assert result["after"]["exact_duplicates"] == 19  # cleaning didn't touch them


# --- validation (planning/milestone-3.md, D6) ---


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [{"action": "fix_type", "column": "nope", "target_type": "numeric"}],
            "doesn't exist",
        ),
        (
            [
                {"action": "handle_missing", "column": "x", "strategy": "drop_rows"},
                {"action": "handle_missing", "column": "x", "strategy": "fill_median"},
            ],
            "Only one handle_missing step per column",
        ),
        (
            [{"action": "remove_duplicates"}, {"action": "remove_duplicates"}],
            "Only one remove_duplicates",
        ),
        (
            [
                {"action": "handle_missing", "column": "x", "strategy": "drop_column"},
                {"action": "fix_type", "column": "x", "target_type": "string"},
            ],
            "removed by a drop_column",
        ),
        (
            [{"action": "handle_missing", "column": "x", "strategy": "fill_constant"}],
            "needs a non-null",
        ),
        (
            [
                {
                    "action": "handle_missing",
                    "column": "x",
                    "strategy": "fill_constant",
                    "value": None,
                }
            ],
            "needs a non-null",
        ),
        (
            [
                {
                    "action": "handle_missing",
                    "column": "x",
                    "strategy": "fill_constant",
                    "value": float("nan"),
                }
            ],
            "finite",
        ),
        (
            [
                {
                    "action": "handle_missing",
                    "column": "x",
                    "strategy": "fill_mean",
                    "value": 1,
                }
            ],
            "only applies to fill_constant",
        ),
        (
            [
                {
                    "action": "handle_outliers",
                    "column": "x",
                    "strategy": "cap_iqr",
                    "method": "zscore",
                }
            ],
            "no effect",
        ),
        (
            [{"action": "handle_outliers", "column": "cat", "strategy": "remove"}],
            "second clean_dataset call",
        ),
    ],
)
def test_static_validation(df_with_missing, steps, message):
    with pytest.raises(InvalidInputError, match=message):
        run_steps(df_with_missing, steps)


@pytest.mark.parametrize(
    "column",
    [
        pd.to_datetime(
            pd.Series(
                ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2030-01-01"]
            )
        ),
        pd.Series(
            ["1", "2", "3", "4", "999", "a", "b", "c", "d", "e"]
        ),  # text with numbers
    ],
)
def test_outlier_steps_only_where_detect_outliers_reports_a_result(column):
    # standard pre-PR review: the gate must match detect_outliers exactly
    df = pd.DataFrame({"c": column})
    for strategy in ("remove", "cap_iqr"):
        step = {"action": "handle_outliers", "column": "c", "strategy": strategy}
        with pytest.raises(InvalidInputError, match="doesn't apply"):
            run_steps(df, [step])


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ({"action": "remove_duplicates", "column": "x"}, "doesn't take 'column'"),
        (
            {
                "action": "handle_outliers",
                "column": "x",
                "strategy": "remove",
                "methd": "zscore",
            },
            "doesn't take 'methd'",
        ),
        ({"action": "fix_type", "column": "x"}, "needs 'target_type'"),
        ({"action": "explode"}, "Unknown action"),
    ],
)
def test_unknown_and_missing_keys_are_errors(df_with_missing, step, message):
    with pytest.raises(InvalidInputError, match=message):
        run_steps(df_with_missing, [step])


@pytest.mark.parametrize("value", ["", "   "])
def test_fill_constant_rejects_empty_strings(df_with_missing, value):
    step = {
        "action": "handle_missing",
        "column": "cat",
        "strategy": "fill_constant",
        "value": value,
    }
    with pytest.raises(InvalidInputError, match="empty"):
        run_steps(df_with_missing, [step])


def test_outlier_step_on_a_column_that_only_becomes_numeric_later():
    df = pd.DataFrame({"p": ["$1,200", "$1,300", "$1,250", "$1,100", "$9,999"]})
    steps = [
        {"action": "fix_type", "column": "p", "target_type": "numeric"},
        {"action": "handle_outliers", "column": "p", "strategy": "remove"},
    ]
    with pytest.raises(InvalidInputError, match="second clean_dataset call"):
        run_steps(df, steps)


def test_fill_median_after_fix_type_in_the_same_call():
    df = pd.DataFrame({"p": ["1,200", None, "1,300", "TBD"]})
    steps = [
        {"action": "fix_type", "column": "p", "target_type": "numeric"},
        {"action": "handle_missing", "column": "p", "strategy": "fill_median"},
    ]
    out, log = run_steps(df, steps)
    assert out["p"].tolist() == [1200.0, 1250.0, 1300.0, 1250.0]
    assert log[0]["values_nulled"] == 1  # "TBD"


def test_lowercase_needs_a_categorical_column():
    df = pd.DataFrame({"n": [1, 2, 3]})
    with pytest.raises(InvalidInputError, match="categorical"):
        run_steps(
            df, [{"action": "fix_type", "column": "n", "target_type": "lowercase"}]
        )


def test_cap_iqr_needs_four_finite_values():
    df = pd.DataFrame({"x": [1.0, 2.0, np.inf, None, None]})
    with pytest.raises(InvalidInputError, match="at least 4 finite"):
        run_steps(
            df, [{"action": "handle_outliers", "column": "x", "strategy": "cap_iqr"}]
        )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (pd.Series([1.0, None]), True, "needs a number"),
        (pd.Series([1.0, None]), "1", "needs a number"),
        (pd.Series([True, None, False], dtype=object), 1, "true or false"),
        (pd.to_datetime(pd.Series(["2024-01-01", None])), "not a date", "date string"),
        (
            pd.to_datetime(pd.Series(["2024-01-01", None])),
            "2024-01-02T00:00:00+02:00",
            "timezone",
        ),
    ],
)
def test_fill_constant_value_must_fit_the_column(column, value, message):
    df = pd.DataFrame({"c": column})
    step = {
        "action": "handle_missing",
        "column": "c",
        "strategy": "fill_constant",
        "value": value,
    }
    with pytest.raises(InvalidInputError, match=message):
        run_steps(df, [step])


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        (pd.Series([1.0, None]), 0, 0.0),
        (pd.Series([True, None, False], dtype=object), False, False),
        (
            pd.to_datetime(pd.Series(["2024-01-01", None])),
            "2024-02-01",
            pd.Timestamp("2024-02-01"),
        ),
        (pd.Series(["a", None, "b"]), 7, "7"),  # other columns store a string
    ],
)
def test_fill_constant_converts_the_value(column, value, expected):
    df = pd.DataFrame({"c": column})
    step = {
        "action": "handle_missing",
        "column": "c",
        "strategy": "fill_constant",
        "value": value,
    }
    out, _ = run_steps(df, [step])
    assert out["c"].iloc[1] == expected


def test_fill_constant_with_a_new_label_on_a_categorical_column():
    # Codex pre-PR review: pandas' Categorical.fillna rejects unknown labels
    df = pd.DataFrame({"c": ["a", "b", None, "a"]})
    steps = [
        {"action": "fix_type", "column": "c", "target_type": "categorical"},
        {
            "action": "handle_missing",
            "column": "c",
            "strategy": "fill_constant",
            "value": "unknown",
        },
    ]
    out, _ = run_steps(df, steps)
    assert out["c"].tolist() == ["a", "b", "unknown", "a"]
    assert isinstance(out["c"].dtype, pd.CategoricalDtype)


def test_numeric_fill_on_a_mixed_string_column():
    # pandas 3 stores this as `str`; fillna with a number must still work
    df = pd.DataFrame({"p": ["1.5", "2", "x", None, "4", "5"]})
    out, _ = run_steps(
        df, [{"action": "handle_missing", "column": "p", "strategy": "fill_mean"}]
    )
    assert out["p"].iloc[3] == pytest.approx(3.125)
    assert (
        out["p"].iloc[2] == "x"
    )  # non-numeric values are left alone, as in the analyzer


def test_pandas_errors_inside_a_step_become_actionable(monkeypatch):
    from data_quality_mcp.cleaner_core import pipeline

    def broken(*args, **kwargs):
        raise TypeError("dtype says no")

    monkeypatch.setattr(pipeline, "fix_types", broken)
    df = pd.DataFrame({"c": ["a", "b"]})
    with pytest.raises(InvalidInputError, match="fix_type step on 'c' failed"):
        run_steps(df, [{"action": "fix_type", "column": "c", "target_type": "string"}])


def test_fill_mode_on_an_all_null_column_is_a_logged_no_op():
    df = pd.DataFrame({"c": [None, None], "d": [1, 2]})
    out, log = run_steps(
        df, [{"action": "handle_missing", "column": "c", "strategy": "fill_mode"}]
    )
    assert out["c"].isna().all()
    assert (log[0]["missing_before"], log[0]["missing_after"]) == (2, 2)


@pytest.mark.parametrize(
    "step",
    [
        {"action": "handle_missing", "column": "x", "strategy": "drop_column"},
        {"action": "handle_missing", "column": "x", "strategy": "drop_rows"},
    ],
)
def test_steps_that_empty_the_dataset_are_rejected(step):
    df = pd.DataFrame({"x": [None, None]})
    with pytest.raises(InvalidInputError, match="remove every"):
        run_steps(df, [step])


def test_steps_keep_caller_order_within_a_group():
    df = pd.DataFrame({"p": ["1", None, "x", "4"]})
    fill_first = [
        {
            "action": "handle_missing",
            "column": "p",
            "strategy": "fill_constant",
            "value": 0,
        },
        {"action": "fix_type", "column": "p", "target_type": "numeric"},
    ]
    _, log = run_steps(df, fill_first)
    assert [e["action"] for e in log] == ["handle_missing", "fix_type"]


def test_values_nulled_counts_coercion_losses_on_string_columns():
    # pandas 2: object column; pandas 3: str column. Both lose two values.
    df = pd.DataFrame({"s": ["1", "x", None, "y", "5"]})
    _, log = run_steps(
        df, [{"action": "fix_type", "column": "s", "target_type": "numeric"}]
    )
    assert log[0]["values_nulled"] == 2
    assert log[0]["original_dtype"] in ("object", "str")
    assert pd.api.types.is_float_dtype(np.dtype(log[0]["new_dtype"]))


def test_log_entries_have_every_field(df_with_missing):
    steps = [
        {"action": "handle_missing", "column": "x", "strategy": "drop_column"},
        {"action": "remove_duplicates"},
    ]
    _, log = run_steps(df_with_missing, steps)
    for entry in log:
        assert len(entry) == 12
    assert log[0]["values_nulled"] is None  # the column is gone
    assert log[1]["column"] is None


# --- run_cleaning end to end on the spec's plan ---


def test_spec_plan_figures(messy_copy, spec_plan):
    result = run_cleaning(spec_plan, file_path=str(messy_copy))

    assert result["output"]["rows"] == 487
    assert result["before"] == {
        "rows": 520,
        "columns": 10,
        "missing_cells": 248,
        "exact_duplicates": 19,
        "quality_score": 77,
        "quality_grade": "C",
    }
    assert result["after"] == {
        "rows": 487,
        "columns": 10,
        "missing_cells": 202,
        "exact_duplicates": 0,
        "quality_score": 86,  # CSV: sale_date reads back as text (D8)
        "quality_grade": "B",
    }
    log = {(e["action"], e["column"]): e for e in result["log"]}
    assert log[("fix_type", "sqft")]["values_nulled"] == 11
    assert log[("handle_missing", "price")]["missing_after"] == 0
    assert log[("handle_outliers", "price")]["outliers_treated"] == 15
    assert log[("remove_duplicates", None)]["rows_removed"] == 18
    assert [e["action"] for e in result["log"]][-1] == "remove_duplicates"
    json.dumps(result, allow_nan=False)


def test_after_matches_profiling_the_written_file(messy_copy, spec_plan):
    result = run_cleaning(spec_plan, file_path=str(messy_copy))
    written = Path(result["output"]["path"])
    report = generate_report(pd.read_csv(written), written.name, "file_path")
    assert result["after"]["quality_score"] == report["dataset"]["quality_score"]


def test_spec_plan_as_xlsx_keeps_dates_typed(messy_copy, spec_plan):
    target = messy_copy.with_name("out.xlsx")
    result = run_cleaning(spec_plan, file_path=str(messy_copy), output_path=str(target))
    assert result["output"]["format"] == "xlsx"
    assert result["after"]["quality_score"] == 88
    assert pd.read_excel(target)["sale_date"].dtype.kind == "M"


def test_inline_input_returns_content(messy_path, spec_plan):
    result = run_cleaning(
        spec_plan,
        inline_content=messy_path.read_text(encoding="utf-8"),
        filename_hint="messy_sample.csv",
        max_inline_output_mb=1,
    )
    assert result["output"]["path"] is None
    returned = pd.read_csv(io.StringIO(result["output"]["content"]))
    assert len(returned) == 487
    assert result["after"]["quality_score"] == 86


def test_nothing_is_written_when_a_step_fails(messy_copy):
    steps = [
        {"action": "remove_duplicates"},
        {"action": "handle_missing", "column": "neighborhood", "strategy": "fill_mean"},
    ]
    with pytest.raises(InvalidInputError):
        run_cleaning(steps, file_path=str(messy_copy))
    assert [p.name for p in messy_copy.parent.iterdir()] == ["messy_sample.csv"]


def test_xlsx_output_too_large_to_reload_blames_the_output(messy_copy):
    from data_quality_mcp.profiler_core.exceptions import OutputWriteError

    with pytest.raises(OutputWriteError, match="Choose a .csv output_path"):
        run_cleaning(
            [{"action": "remove_duplicates"}],
            file_path=str(messy_copy),
            output_path=str(messy_copy.with_name("out.xlsx")),
            max_xlsx_uncompressed_mb=0.01,
        )
    assert not messy_copy.with_name("out.xlsx").exists()


def test_output_path_with_inline_input_is_rejected(tmp_path):
    with pytest.raises(InvalidInputError, match="only applies to file input"):
        run_cleaning(
            [{"action": "remove_duplicates"}],
            inline_content="a\n1\n",
            filename_hint="x.csv",
            output_path=str(tmp_path / "x.csv"),
        )


def test_step_count_bounds():
    with pytest.raises(InvalidInputError, match="at least one"):
        run_cleaning([], inline_content="a\n1\n", filename_hint="x.csv")
    with pytest.raises(InvalidInputError, match="At most 2"):
        run_cleaning(
            [{"action": "remove_duplicates"}] * 3,
            inline_content="a\n1\n",
            filename_hint="x.csv",
            max_steps=2,
        )
