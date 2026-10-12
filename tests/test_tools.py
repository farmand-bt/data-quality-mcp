"""The MCP layer, exercised the way a real client sees it."""

import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from data_quality_mcp.profiler_core.schema import DatasetReport
from data_quality_mcp.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client():
    async with Client(create_server(), raise_exceptions=True) as c:
        yield c


async def test_profile_dataset_is_listed_with_schemas(client):
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    tool = tools["profile_dataset"]

    assert tool.annotations.read_only_hint is True
    assert tool.annotations.open_world_hint is False
    assert "absolute" in tool.description.lower()
    assert tool.input_schema["required"] == ["file_path"]
    assert set(tool.output_schema["properties"]) == set(DatasetReport.__annotations__)


async def test_profile_dataset_returns_structured_report(client, messy_path: Path):
    result = await client.call_tool("profile_dataset", {"file_path": str(messy_path)})

    assert result.is_error is False
    report = result.structured_content
    assert report["dataset"]["name"] == "messy_sample.csv"
    assert report["dataset"]["rows"] == 520
    assert report["columns"]["sqft"]["inferred_type"] == "mixed"
    assert report["duplicates"] is None


@pytest.mark.parametrize(
    ("file_path", "expected"),
    [
        ("does/not/exist.csv", "absolute path"),
        ("pyproject.toml", "Unsupported file type"),
    ],
)
async def test_domain_errors_reach_the_agent(client, file_path: str, expected: str):
    result = await client.call_tool("profile_dataset", {"file_path": file_path})

    assert result.is_error is True
    # A ToolError's message is passed through; a crash would only say
    # "Error executing tool profile_dataset" with no detail.
    assert expected in result.content[0].text


async def test_stdio_transport_end_to_end(messy_path: Path):
    """Spawn the real server process: proves nothing pollutes stdout."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "data_quality_mcp.server"]
    )
    async with Client(params) as client:
        result = await client.call_tool(
            "profile_dataset", {"file_path": str(messy_path)}
        )
    assert result.is_error is False
    assert result.structured_content["dataset"]["rows"] == 520
