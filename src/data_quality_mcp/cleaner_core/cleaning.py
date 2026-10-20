"""The cleaning tool's single entry point: load, clean, write, and report.

The MCP tool only passes its arguments here; everything else happens in the
core (planning/milestone-3.md, layer boundary).
"""

from collections.abc import Sequence
from pathlib import Path

from data_quality_mcp import config
from data_quality_mcp.cleaner_core.pipeline import run_steps
from data_quality_mcp.cleaner_core.schema import CleaningStep
from data_quality_mcp.cleaner_core.writer import (
    check_output_path,
    serialize,
    write_output,
)
from data_quality_mcp.cleaner_core.writer import inline_content as encode_inline
from data_quality_mcp.profiler_core.exceptions import InvalidInputError
from data_quality_mcp.profiler_core.json_safe import to_json_safe
from data_quality_mcp.profiler_core.loader import load_input, parse_bytes
from data_quality_mcp.profiler_core.report import (
    generate_report,
    prepare_frame,
    quality_snapshot,
)
from data_quality_mcp.profiler_core.schema import CleaningResult


def run_cleaning(
    steps: Sequence[CleaningStep],
    file_path: str | None = None,
    inline_content: str | None = None,
    filename_hint: str | None = None,
    output_path: str | None = None,
    *,
    max_steps: int = config.MAX_CLEANING_STEPS,
    max_inline_output_mb: float = config.MAX_INLINE_OUTPUT_MB,
    max_file_size_mb: float = config.MAX_FILE_SIZE_MB,
    max_rows: int = config.MAX_ROWS,
    max_xlsx_uncompressed_mb: float = config.MAX_XLSX_UNCOMPRESSED_MB,
    max_xlsx_entries: int = config.MAX_XLSX_ENTRIES,
    allow_network_paths: bool = config.ALLOW_NETWORK_PATHS,
    suffix: str = config.CLEANED_SUFFIX,
    iqr_multiplier: float = config.OUTLIER_IQR_MULTIPLIER,
    zscore_threshold: float = config.OUTLIER_ZSCORE_THRESHOLD,
) -> CleaningResult:
    """Apply `steps` to a dataset and return the output, quality before and
    after, and the log.

    The output goes back the way the input came: a new file next to the input
    (or at `output_path`) for `file_path`, inline content for
    `inline_content`. Nothing is written unless every step succeeds, and
    `after` profiles the serialised output loaded back, so it equals what
    profile_dataset reports for that file.
    """
    if not steps:
        raise InvalidInputError("Give at least one cleaning step.")
    if len(steps) > max_steps:
        raise InvalidInputError(f"At most {max_steps} steps per call.")
    if output_path is not None and inline_content is not None:
        raise InvalidInputError(
            "'output_path' only applies to file input. With 'inline_content', the "
            "cleaned data comes back in the result."
        )
    target = extension = None
    if output_path is not None:
        target, extension = check_output_path(
            output_path, allow_network_paths=allow_network_paths
        )

    limits = {
        "max_file_size_mb": max_file_size_mb,
        "max_rows": max_rows,
        "max_xlsx_uncompressed_mb": max_xlsx_uncompressed_mb,
        "max_xlsx_entries": max_xlsx_entries,
    }
    loaded = load_input(
        file_path,
        inline_content,
        filename_hint,
        allow_network_paths=allow_network_paths,
        **limits,
    )
    extension = extension or loaded.extension
    df = prepare_frame(loaded.df, loaded.source)
    before = quality_snapshot(generate_report(df, loaded.name, loaded.source))

    cleaned, log = run_steps(
        df, steps, iqr_multiplier=iqr_multiplier, zscore_threshold=zscore_threshold
    )
    raw = serialize(cleaned, extension)

    content = out_path = None
    if loaded.source == "inline_content":
        content = encode_inline(raw, extension, max_mb=max_inline_output_mb)

    del limits["max_file_size_mb"]  # our own output: no size limit on reload
    reloaded = parse_bytes(raw, extension, **limits)
    after = quality_snapshot(generate_report(reloaded, loaded.name, loaded.source))

    if loaded.source == "file_path":  # written last: only once all else succeeded
        written = write_output(
            raw,
            target=target,
            input_path=Path(str(file_path)).expanduser(),
            extension=extension,
            suffix=suffix,
        )
        out_path = str(written)

    return to_json_safe(
        {
            "dataset": {
                "name": loaded.name,
                "source": loaded.source,
                "rows": len(df),
                "columns": len(df.columns),
            },
            "output": {
                "path": out_path,
                "content": content,
                "format": extension.lstrip("."),
                "rows": len(cleaned),
                "columns": len(cleaned.columns),
            },
            "before": before,
            "after": after,
            "log": log,
        }
    )
