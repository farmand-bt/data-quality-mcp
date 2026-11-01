"""The cleaning tool's input contract: one TypedDict per step type.

The output types (CleaningResult and its parts) live with the rest of the
output contract in profiler_core/schema.py. `typing_extensions.TypedDict` is
required for Pydantic on Python < 3.12, as there.
"""

from typing import Literal

from typing_extensions import NotRequired, TypedDict

MissingStrategy = Literal[
    "drop_rows",
    "drop_column",
    "fill_mean",
    "fill_median",
    "fill_mode",
    "fill_constant",
    "ffill",
    "bfill",
]
TargetType = Literal[
    "numeric", "datetime", "categorical", "string", "boolean", "lowercase"
]
OutlierStrategy = Literal["remove", "cap_iqr", "replace_nan"]
OutlierMethod = Literal["iqr", "zscore", "sentinel"]
DuplicateKeep = Literal["first", "last", "none"]


class HandleMissingStep(TypedDict):
    """Handle missing values in one column. `value` is required for
    fill_constant and not allowed with any other strategy."""

    action: Literal["handle_missing"]
    column: str
    strategy: MissingStrategy
    value: NotRequired[str | int | float | bool | None]


class RemoveDuplicatesStep(TypedDict):
    """Remove exact duplicate rows. keep: "first" (default) or "last" keeps
    one copy of each group; the string "none" (not JSON null) drops every copy."""

    action: Literal["remove_duplicates"]
    keep: NotRequired[DuplicateKeep]


class FixTypeStep(TypedDict):
    """Convert one column. Values that can't be converted become null and are
    counted in the log's values_nulled."""

    action: Literal["fix_type"]
    column: str
    target_type: TargetType


class HandleOutliersStep(TypedDict):
    """Treat the outliers of one numeric column. `method` ("iqr", the default,
    "zscore" or "sentinel") chooses the rows for remove and replace_nan;
    cap_iqr always clips to the IQR fences. "sentinel" needs `values`: the
    placeholder codes the user confirmed, each one of the column's
    possible_sentinels (not allowed with any other method)."""

    action: Literal["handle_outliers"]
    column: str
    strategy: OutlierStrategy
    method: NotRequired[OutlierMethod]
    values: NotRequired[list[float]]


# Discriminated by `action`; tools/ adds Pydantic's discriminator annotation
# for the published input schema, so the core stays free of schema libraries.
CleaningStep = (
    HandleMissingStep | RemoveDuplicatesStep | FixTypeStep | HandleOutliersStep
)
