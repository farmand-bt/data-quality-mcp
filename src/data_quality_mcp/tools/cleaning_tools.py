"""MCP-facing cleaning tool: a thin adapter over cleaner_core.run_cleaning."""

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from data_quality_mcp.cleaner_core import run_cleaning
from data_quality_mcp.profiler_core import DataQualityError
from data_quality_mcp.profiler_core.schema import CleaningResult
from data_quality_mcp.tools.inputs import (
    FilenameHint,
    FilePath,
    InlineContent,
    OutputPath,
    Steps,
)

# Writes a new file (never overwrites one), so it is neither read-only nor
# destructive; each call creates a new file, so it isn't idempotent either.
_WRITES_NEW_FILE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


def clean_dataset(
    steps: Steps,
    file_path: FilePath = None,
    inline_content: InlineContent = None,
    filename_hint: FilenameHint = None,
    output_path: OutputPath = None,
) -> CleaningResult:
    """Clean a CSV or Excel dataset with a list of steps, without changing the original.

    Use this after profile_dataset, to apply a cleaning plan the user agreed
    to. The original is never modified and no existing file is ever
    overwritten.

    Steps (objects with an "action"; at most one step per action and column):
    - handle_missing {column, strategy, value?}: strategy is drop_rows,
      drop_column, fill_mean, fill_median (numeric columns only, computed
      from finite numbers: inf/-inf are ignored), fill_mode,
      fill_constant (needs "value": a number for numeric columns, true/false
      for boolean ones, a date string for dates), ffill or bfill.
    - remove_duplicates {keep?}: "first" (default) or "last" keeps one copy of
      each exact-duplicate group; the string "none" (not JSON null) drops
      every copy.
    - fix_type {column, target_type}: numeric (strips , $ % € £ and spaces),
      datetime, categorical, string, boolean (true/yes/y/1 and false/no/n/0),
      or lowercase (normalizes category labels such as Male/MALE/male).
      Values that can't be converted become null; the log counts them in
      values_nulled. datetime: timestamps with different UTC offsets are
      converted to UTC; a column mixing timestamps with and without an
      offset is rejected (make them consistent first).
    - handle_outliers {column, strategy, method?, values?}: strategy is
      remove, cap_iqr (clips to the IQR fences) or replace_nan. method "iqr"
      (default) or "zscore" chooses the rows for remove/replace_nan. The rows
      are exactly the ones detect_outliers reports for the input. method
      "sentinel" acts only on the placeholder codes listed in "values"
      (1 to 10 numbers, e.g. [-1000, 9999999]), each of which must be one of
      the column's possible_sentinels in the input; use it with replace_nan
      (usual) or remove, not cap_iqr. Confirm the codes with the user first:
      possible_sentinels are suspects, and a real extreme value can be among
      them. "values" is only allowed with method "sentinel". A column
      must already be numeric (or mostly numeric) in the input: if it only
      becomes numeric through fix_type, convert it in this call and treat its
      outliers in a second call on the output.

    Order: steps run in the order given, except that outlier removal runs
    after type fixes and fills, and drop_rows, drop_column and
    remove_duplicates run last, so outlier rows still match the input. List
    fix_type before a fill on the same column.

    Input and output: give file_path, or inline_content plus filename_hint.
    - File input writes a new file and returns its path in output.path: by
      default '<name>_cleaned.<ext>' next to the input (then _cleaned_2, ...),
      or at output_path (absolute, .csv or .xlsx, must not exist).
    - Inline input returns the cleaned data in output.content instead (CSV as
      text, .xlsx as base64), up to 1 MB of encoded text (about 0.75 MB of
      .xlsx data). Larger results need file input.
    Nothing is written unless every step succeeds.

    Output: dataset (the input), output (path or content, format, rows,
    columns), before and after (rows, columns, missing_cells,
    exact_duplicates, quality_score, quality_grade; after is measured on the
    written output, so it matches profile_dataset on that file; dates written
    to a CSV as ISO text count as typed, so converting them pays off there
    too), and log (one entry per step in execution order: rows
    before/after/removed, values_nulled, dtypes, missing counts and
    outliers_treated, null where not applicable).

    Errors say what to fix: an unknown column, a strategy that doesn't fit
    the column, conflicting steps, an existing output_path, or steps that
    would remove every row.
    """
    try:
        return run_cleaning(
            steps,
            file_path=file_path,
            inline_content=inline_content,
            filename_hint=filename_hint,
            output_path=output_path,
        )
    except DataQualityError as exc:
        raise ToolError(str(exc)) from exc


def register(app: MCPServer) -> None:
    """Register the cleaning tool on `app`."""
    app.add_tool(clean_dataset, title="Clean dataset", annotations=_WRITES_NEW_FILE)
