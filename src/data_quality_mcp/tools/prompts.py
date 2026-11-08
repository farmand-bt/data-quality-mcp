"""MCP prompts: reusable, user-invoked templates.

Prompts are static text: they guide the agent through a workflow using this
server's tools and never touch data themselves.
"""

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.prompts import Prompt
from pydantic import Field


def review_data_quality(
    file_path: Annotated[
        str,
        Field(
            description=(
                "Path to the .csv or .xlsx file to review (on a remote server, "
                "the agent reads it and sends its contents)."
            )
        ),
    ],
) -> str:
    """Review a dataset's quality and propose an ordered cleaning plan."""
    return f"""Review the data quality of this file: {file_path}

1. Call the `profile_dataset` tool with that exact path. If the server
   answers that it can't read files (a remote server), read the file yourself
   and send its contents as `inline_content`, with its name as
   `filename_hint`. If it returns any other error, explain the error and how
   to fix it, then stop.
2. State the quality score and grade in one sentence. If you need to explain
   how the score is computed, read the resource dataquality://docs/quality-score.
3. Go through the `recommendations` in order (critical first). Group related
   findings, and explain each issue in plain language: what is wrong, which
   columns are affected, and why it matters for analysis.
4. Propose an ordered cleaning plan, in this order, skipping steps that don't
   apply: (a) remove exact duplicate rows, (b) fix type mismatches and
   inconsistent labels, (c) handle missing values (if a column's
   `missing_related_to` lists another column, say that a mean or median
   fill would bias it), (d) review and treat outliers. For each step, name
   the columns and a concrete approach.
5. Point out anything that needs a human decision: whether near-duplicates,
   outliers or `possible_sentinels` are real data or errors (placeholder
   codes are only suspects until the user confirms them), and which record
   is right where a key column has conflicting values.

Do not modify anything yet. If the user agrees to the plan, apply it with
`clean_dataset`, which writes a new file, or returns the cleaned data inline,
and never changes the original.
Keep the review concise and specific to this dataset.
"""


def register(app: MCPServer) -> None:
    """Register the prompts on `app`."""
    app.add_prompt(
        Prompt.from_function(review_data_quality, title="Review data quality")
    )
