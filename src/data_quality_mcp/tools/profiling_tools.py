"""MCP-facing profiling tools: thin adapters over profiler_core.

Each tool parses arguments, calls the core, and returns its result. Domain
errors from the core are turned into ToolError, whose message reaches the
calling agent; any other exception is reported generically by the SDK.
"""

from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from data_quality_mcp.profiler_core import DataQualityError, generate_report, load_data
from data_quality_mcp.profiler_core.schema import DatasetReport


def profile_dataset(
    file_path: Annotated[
        str,
        Field(
            description=(
                "Absolute path to a .csv or .xlsx file on the machine running "
                "this server, e.g. 'C:\\data\\sales.csv' or "
                "'/home/me/data/sales.csv'."
            )
        ),
    ],
) -> DatasetReport:
    """Profile a tabular dataset and return a structured data quality report.

    Use this tool to assess a CSV or Excel file before analyzing, cleaning, or
    modeling it: it finds missing values, columns whose values don't match their
    storage type (e.g. numbers stored as text, dates in several formats), and
    other per-column issues, and scores overall quality. It only reads the file;
    nothing is modified. The analysis is deterministic.

    Input: `file_path` must be an absolute path. Relative paths resolve against
    the server's working directory, which is usually not what you expect.
    Supported formats: .csv (comma- or semicolon-separated) and .xlsx (first
    sheet). Limits: 50 MB and 500,000 rows.

    Output sections:
    - dataset: name, rows, columns, quality_score (0-100, higher is better) and
      quality_grade (A >= 90, B >= 80, C >= 70, D >= 60, otherwise F).
    - columns: one entry per column with pandas_dtype (how it is stored),
      inferred_type (numeric, categorical, datetime, boolean, text or mixed),
      type_mismatch, missing_count, missing_pct, stats (fields depend on
      inferred_type; for numeric/mixed columns, non_numeric_examples shows the
      offending values), and warnings (plain-language issues with suggested
      fixes). Start with the warnings.
    - duplicates, correlations, and each column's outliers: null means "not
      computed by this server version", NOT "none found". Do not report these
      as clean.
    - recommendations: currently always empty.
    - generated_at: UTC timestamp.

    Errors: the message says what to fix, e.g. file not found (check the path is
    absolute), unsupported file type, file too large, or unparseable content.
    """
    try:
        df = load_data(file_path=file_path)
        return generate_report(df, name=Path(file_path).name, source="file_path")
    except DataQualityError as exc:
        raise ToolError(str(exc)) from exc


def register(app: MCPServer) -> None:
    """Register the profiling tools on `app`."""
    app.add_tool(
        profile_dataset,
        title="Profile dataset",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
