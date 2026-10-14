"""MCP resources: static, read-only reference material for agents.

Both resources are stateless (no per-caller data), so they behave the same
over stdio and HTTP. Their content is generated from the code (the report
TypedDicts and the scoring constants), so it can't drift from the behaviour.
"""

import json

from mcp.server import MCPServer
from pydantic import TypeAdapter

from data_quality_mcp.profiler_core.schema import DatasetReport
from data_quality_mcp.profiler_core.scoring import quality_score_explanation

REPORT_SCHEMA_URI = "dataquality://schema/report"
QUALITY_SCORE_URI = "dataquality://docs/quality-score"


def report_schema() -> str:
    """JSON Schema of the full report returned by profile_dataset.

    Read this to understand every field of the report, or to validate one.
    """
    return json.dumps(TypeAdapter(DatasetReport).json_schema(), indent=2)


def quality_score_docs() -> str:
    """How quality_score and quality_grade are computed, and what the outlier
    methods mean. Read this before explaining a score to a user."""
    return quality_score_explanation()


def register(app: MCPServer) -> None:
    """Register the resources on `app`."""
    app.resource(
        REPORT_SCHEMA_URI,
        name="report-schema",
        title="Report JSON Schema",
        mime_type="application/schema+json",
    )(report_schema)
    app.resource(
        QUALITY_SCORE_URI,
        name="quality-score",
        title="How the quality score works",
        mime_type="text/markdown",
    )(quality_score_docs)
