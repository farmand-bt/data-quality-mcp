"""Argument types shared by the MCP tools (descriptions are written for the
calling model). Types only: this module registers nothing."""

from typing import Annotated

from pydantic import Field

from data_quality_mcp import config
from data_quality_mcp.cleaner_core.schema import (
    CleaningStep,
    FixTypeStep,
    HandleMissingStep,
    HandleOutliersStep,
    RemoveDuplicatesStep,
)

# Publish and enforce "no unknown keys" (additionalProperties: false). Pydantic
# would otherwise drop them silently; the core rejects them too, for direct calls.
for _step in (HandleMissingStep, RemoveDuplicatesStep, FixTypeStep, HandleOutliersStep):
    _step.__pydantic_config__ = {"extra": "forbid"}

FilePath = Annotated[
    str | None,
    Field(
        description=(
            "Absolute path to a .csv or .xlsx file on the machine running this "
            "server, e.g. 'C:\\data\\sales.csv' or '/home/me/data/sales.csv'. "
            "Network paths (\\\\server\\share\\...) are not accepted. Give either "
            "this or inline_content, not both."
        )
    ),
]

InlineContent = Annotated[
    str | None,
    Field(
        description=(
            "The data itself, instead of a file path: CSV as plain text (the "
            "file's contents, header row first), or an .xlsx file as base64. "
            "Use it when the server can't see the file, and for small data "
            "only: every character passes through the conversation. Requires "
            "filename_hint."
        )
    ),
]

FilenameHint = Annotated[
    str | None,
    Field(
        description=(
            "Required with inline_content: a file name whose extension sets the "
            "format, e.g. 'sales.csv' (CSV text) or 'report.xlsx' (base64). It "
            "becomes the dataset's name."
        )
    ),
]

Steps = Annotated[
    list[Annotated[CleaningStep, Field(discriminator="action")]],
    Field(
        min_length=1,
        max_length=config.MAX_CLEANING_STEPS,
        description=(
            "Cleaning steps, each an object with an 'action': handle_missing, "
            "remove_duplicates, fix_type or handle_outliers. At most one step "
            "per action and column."
        ),
    ),
]

OutputPath = Annotated[
    str | None,
    Field(
        description=(
            "Optional, file input only: absolute path for the new file, ending "
            "in .csv or .xlsx (the extension sets the format). It must not "
            "exist yet. Omit it to write '<name>_cleaned.<ext>' next to the "
            "input."
        )
    ),
]
