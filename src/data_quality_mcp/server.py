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
from data_quality_mcp.tools import profiling_tools

SERVER_NAME = "data-quality"

INSTRUCTIONS = (
    "Deterministic data quality profiling for CSV and Excel files. Call "
    "profile_dataset with an absolute file path to get a structured report: "
    "quality score and grade, per-column inferred types, type mismatches, "
    "missing values, statistics, and plain-language warnings. Fields that are "
    "null have not been computed by this server version."
)


def create_server() -> MCPServer:
    """Build the app and register all tools on it."""
    app = MCPServer(SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)
    profiling_tools.register(app)
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
