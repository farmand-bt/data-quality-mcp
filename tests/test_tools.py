"""The MCP layer, exercised the way a real client sees it."""

import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from data_quality_mcp.profiler_core.schema import (
    CorrelationResult,
    DatasetReport,
    DuplicateResult,
    OutlierResult,
)
from data_quality_mcp.server import create_server
from data_quality_mcp.tools.resources import QUALITY_SCORE_URI, REPORT_SCHEMA_URI

pytestmark = pytest.mark.anyio

TOOL_RESULTS = {
    "profile_dataset": DatasetReport,
    "detect_outliers": OutlierResult,
    "find_duplicates": DuplicateResult,
    "analyze_correlations": CorrelationResult,
}


@pytest.fixture
async def client():
    async with Client(create_server(), raise_exceptions=True) as c:
        yield c


# --- tools ---


async def test_all_tools_listed_read_only_with_schemas(client):
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == set(TOOL_RESULTS)

    for name, result_type in TOOL_RESULTS.items():
        tool = tools[name]
        assert tool.annotations.read_only_hint is True, name
        assert tool.annotations.open_world_hint is False, name
        assert "absolute" in tool.description.lower(), name
        assert tool.input_schema["required"] == ["file_path"], name
        properties = set(tool.output_schema["properties"])
        assert properties == set(result_type.__annotations__), name


async def test_profile_dataset_returns_structured_report(client, messy_path: Path):
    result = await client.call_tool("profile_dataset", {"file_path": str(messy_path)})

    assert result.is_error is False
    report = result.structured_content
    assert report["dataset"]["name"] == "messy_sample.csv"
    assert report["dataset"]["rows"] == 520
    assert report["dataset"]["quality_grade"] == "C"
    assert report["columns"]["sqft"]["inferred_type"] == "mixed"
    assert report["columns"]["price"]["outliers"]["iqr_count"] == 15
    assert report["duplicates"]["exact_count"] == 19
    assert report["recommendations"][0]["severity"] in ("critical", "warning")


@pytest.mark.parametrize(
    ("tool", "section"),
    [
        ("detect_outliers", "outliers"),
        ("find_duplicates", "duplicates"),
        ("analyze_correlations", "correlations"),
    ],
)
async def test_focused_tools_return_only_their_section(
    client, messy_path: Path, tool: str, section: str
):
    result = await client.call_tool(tool, {"file_path": str(messy_path)})

    assert result.is_error is False
    content = result.structured_content
    assert set(content) == {"dataset", section}
    assert content["dataset"] == {
        "name": "messy_sample.csv",
        "source": "file_path",
        "rows": 520,
        "columns": 10,
    }


async def test_focused_tools_match_the_full_report(client, messy_path: Path):
    args = {"file_path": str(messy_path)}
    report = (await client.call_tool("profile_dataset", args)).structured_content
    outliers = (await client.call_tool("detect_outliers", args)).structured_content
    duplicates = (await client.call_tool("find_duplicates", args)).structured_content

    assert outliers["outliers"]["price"] == report["columns"]["price"]["outliers"]
    assert outliers["outliers"]["neighborhood"] is None
    assert duplicates["duplicates"] == report["duplicates"]


@pytest.mark.parametrize("tool", sorted(TOOL_RESULTS))
@pytest.mark.parametrize(
    ("file_path", "expected"),
    [
        ("does/not/exist.csv", "absolute path"),
        ("pyproject.toml", "Unsupported file type"),
    ],
)
async def test_domain_errors_reach_the_agent(
    client, tool: str, file_path: str, expected: str
):
    result = await client.call_tool(tool, {"file_path": file_path})

    assert result.is_error is True
    # A ToolError's message is passed through; a crash would only say
    # "Error executing tool <name>" with no detail.
    assert expected in result.content[0].text


# --- resources ---


async def test_resources_listed(client):
    resources = {str(r.uri): r for r in (await client.list_resources()).resources}
    assert set(resources) == {REPORT_SCHEMA_URI, QUALITY_SCORE_URI}
    assert resources[REPORT_SCHEMA_URI].mime_type == "application/schema+json"
    assert resources[QUALITY_SCORE_URI].mime_type == "text/markdown"


async def test_report_schema_resource_is_json_schema(client):
    result = await client.read_resource(REPORT_SCHEMA_URI)
    schema = json.loads(result.contents[0].text)

    assert set(schema["properties"]) == set(DatasetReport.__annotations__)
    assert {"ColumnReport", "OutlierInfo", "Recommendation"} <= set(schema["$defs"])


async def test_quality_score_resource(client):
    text = (await client.read_resource(QUALITY_SCORE_URI)).contents[0].text
    assert text.startswith("# How the data quality score is computed")
    assert "A >= 90" in text


# --- prompts ---


async def test_review_prompt(client):
    prompts = {p.name: p for p in (await client.list_prompts()).prompts}
    prompt = prompts["review_data_quality"]
    assert prompt.title == "Review data quality"
    assert [(a.name, a.required) for a in prompt.arguments] == [("file_path", True)]

    result = await client.get_prompt(
        "review_data_quality", {"file_path": "C:\\data\\sales.csv"}
    )
    text = result.messages[0].content.text
    assert result.messages[0].role == "user"
    assert "C:\\data\\sales.csv" in text
    assert "profile_dataset" in text


# --- transport ---


async def test_stdio_transport_end_to_end(messy_path: Path):
    """Spawn the real server process: proves nothing pollutes stdout."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "data_quality_mcp.server"]
    )
    async with Client(params) as client:
        result = await client.call_tool(
            "profile_dataset", {"file_path": str(messy_path)}
        )
        resources = (await client.list_resources()).resources
    assert result.is_error is False
    assert result.structured_content["dataset"]["rows"] == 520
    assert len(resources) == 2
