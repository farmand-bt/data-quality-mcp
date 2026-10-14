"""MCP server entry point.

Builds the MCPServer app and registers every tools module on it. Run over stdio
with `data-quality-mcp` or `python -m data_quality_mcp.server`; inspect with
`uv run mcp dev src/data_quality_mcp/server.py:app`.

Over stdio, stdout carries the JSON-RPC stream: never print. Logs go to stderr.
"""

import logging
import sys

from mcp.server import MCPServer

from data_quality_mcp import __version__
from data_quality_mcp.tools import profiling_tools, prompts, resources

SERVER_NAME = "data-quality"

INSTRUCTIONS = (
    "Deterministic data quality profiling for CSV and Excel files (read-only; "
    "always pass absolute file paths). profile_dataset returns the full report: "
    "quality score and grade, prioritized recommendations, and per-column types, "
    "type mismatches, missing values, statistics, outliers and warnings, plus "
    "duplicate rows and correlations. For a narrow question use detect_outliers, "
    "find_duplicates or analyze_correlations. The resource "
    "dataquality://docs/quality-score explains the score, and "
    "dataquality://schema/report is the report's JSON Schema. The "
    "review_data_quality prompt runs a complete review."
)


def create_server() -> MCPServer:
    """Build the app and register every tools module on it."""
    app = MCPServer(SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)
    profiling_tools.register(app)
    resources.register(app)
    prompts.register(app)
    return app


app = create_server()


def main() -> None:
    """Console-script entry point: serve over stdio."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app.run()


if __name__ == "__main__":
    main()
