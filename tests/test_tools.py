"""The MCP layer, exercised the way a real client sees it."""

import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from data_quality_mcp.profiler_core.schema import (
    CleaningResult,
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


async def test_all_tools_listed_with_schemas(client):
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == set(TOOL_RESULTS) | {"clean_dataset"}

    for name, result_type in TOOL_RESULTS.items():
        tool = tools[name]
        assert tool.annotations.read_only_hint is True, name
        assert tool.annotations.open_world_hint is False, name
        assert "absolute" in tool.description.lower(), name
        assert "inline_content" in tool.description, name
        assert set(tool.input_schema["properties"]) == {
            "file_path",
            "inline_content",
            "filename_hint",
        }, name
        assert not tool.input_schema.get("required"), name  # one of two is needed
        properties = set(tool.output_schema["properties"])
        assert properties == set(result_type.__annotations__), name


async def test_clean_dataset_schema_and_annotations(client):
    tool = {t.name: t for t in (await client.list_tools()).tools}["clean_dataset"]
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False  # never overwrites a file
    assert tool.annotations.idempotent_hint is False  # each call writes a new file
    assert tool.input_schema["required"] == ["steps"]
    steps = tool.input_schema["properties"]["steps"]
    assert (steps["minItems"], steps["maxItems"]) == (1, 50)
    assert steps["items"]["discriminator"]["propertyName"] == "action"
    assert set(steps["items"]["discriminator"]["mapping"]) == {
        "handle_missing",
        "remove_duplicates",
        "fix_type",
        "handle_outliers",
    }
    properties = set(tool.output_schema["properties"])
    assert properties == set(CleaningResult.__annotations__)
    for name in (
        "HandleMissingStep",
        "RemoveDuplicatesStep",
        "FixTypeStep",
        "HandleOutliersStep",
    ):
        assert tool.input_schema["$defs"][name]["additionalProperties"] is False
    outlier_step = tool.input_schema["$defs"]["HandleOutliersStep"]["properties"]
    assert outlier_step["method"]["enum"] == ["iqr", "zscore", "sentinel"]
    assert outlier_step["values"] == {
        "items": {"type": "number"},
        "title": "Values",
        "type": "array",
    }


@pytest.mark.parametrize("values", [[True], ["5000"]])
async def test_sentinel_values_are_not_coerced(client, messy_copy: Path, values):
    # Lax validation would turn true into 1.0 and "5000" into 5000.0
    step = {
        "action": "handle_outliers",
        "column": "price",
        "strategy": "replace_nan",
        "method": "sentinel",
        "values": values,
    }
    result = await client.call_tool(
        "clean_dataset", {"steps": [step], "file_path": str(messy_copy)}
    )
    assert result.is_error is True
    assert [p.name for p in messy_copy.parent.iterdir()] == ["messy_sample.csv"]


async def test_clean_dataset_sentinel_step(client, messy_copy: Path):
    step = {
        "action": "handle_outliers",
        "column": "price",
        "strategy": "replace_nan",
        "method": "sentinel",
        "values": [-1000, 5000, 9999999],  # JSON integers
    }
    result = await client.call_tool(
        "clean_dataset", {"steps": [step], "file_path": str(messy_copy)}
    )
    assert result.is_error is False
    assert result.structured_content["log"][0]["values_nulled"] == 10


async def test_new_report_fields_reach_the_agent(client, messy_path: Path):
    report = (
        await client.call_tool("profile_dataset", {"file_path": str(messy_path)})
    ).structured_content
    assert report["columns"]["school_rating"]["missing_related_to"][0]["column"] == (
        "age_years"
    )
    assert len(report["columns"]["price"]["outliers"]["possible_sentinels"]) == 3
    assert report["duplicates"]["key_columns"][0]["sample_indices"] == [13, 65]
    outliers = (
        await client.call_tool("detect_outliers", {"file_path": str(messy_path)})
    ).structured_content
    assert outliers["outliers"]["price"]["possible_sentinels"][0]["value"] == -1000.0


@pytest.mark.parametrize("name", ["dates.CSV", "dates.csv"])
async def test_iso_dates_in_a_csv_file_or_inline_are_typed(client, tmp_path, name):
    text = "when,x\n2020-01-01,1\n2020-02-03,2\n2020-03-04,3\n"
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    for args in (
        {"file_path": str(path)},
        {"inline_content": text, "filename_hint": name},
    ):
        report = (await client.call_tool("profile_dataset", args)).structured_content
        assert report["columns"]["when"]["type_mismatch"] is False, args


async def test_clean_dataset_schema_rejects_unknown_keys(client, messy_copy: Path):
    result = await client.call_tool(
        "clean_dataset",
        {
            "steps": [{"action": "remove_duplicates", "column": "id"}],
            "file_path": str(messy_copy),
        },
    )
    assert result.is_error is True
    assert [p.name for p in messy_copy.parent.iterdir()] == ["messy_sample.csv"]


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


@pytest.mark.parametrize("tool", sorted(TOOL_RESULTS))
async def test_inline_csv_gives_the_same_result_as_the_file(
    client, messy_path: Path, tool: str
):
    by_path = await client.call_tool(tool, {"file_path": str(messy_path)})
    inline = await client.call_tool(
        tool,
        {
            "inline_content": messy_path.read_text(encoding="utf-8"),
            "filename_hint": "messy_sample.csv",
        },
    )
    assert inline.is_error is False
    a, b = by_path.structured_content, inline.structured_content
    assert b["dataset"]["source"] == "inline_content"
    for result in (a, b):
        result.pop("generated_at", None)
        result["dataset"].pop("source")
    assert a == b


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({}, "neither"),
        ({"file_path": "x.csv", "inline_content": "a\n1\n"}, "both"),
        ({"inline_content": "a\n1\n"}, "filename_hint"),
    ],
)
async def test_input_errors_reach_the_agent(client, args: dict, expected: str):
    result = await client.call_tool("profile_dataset", args)
    assert result.is_error is True
    assert expected in result.content[0].text


# --- cleaning ---


async def test_clean_dataset_writes_a_new_file(client, messy_copy: Path, spec_plan):
    original = messy_copy.read_bytes()
    result = await client.call_tool(
        "clean_dataset", {"steps": spec_plan, "file_path": str(messy_copy)}
    )

    assert result.is_error is False
    content = result.structured_content
    output = Path(content["output"]["path"])
    assert output == messy_copy.with_name("messy_sample_cleaned.csv")
    assert output.is_file()
    assert messy_copy.read_bytes() == original  # the input is untouched
    scores = (content["before"]["quality_score"], content["after"]["quality_score"])
    assert scores == (77, 88)
    assert content["log"][-1]["action"] == "remove_duplicates"

    profiled = await client.call_tool("profile_dataset", {"file_path": str(output)})
    assert profiled.structured_content["dataset"]["quality_score"] == 88


async def test_clean_dataset_inline_returns_the_data(client):
    result = await client.call_tool(
        "clean_dataset",
        {
            "steps": [{"action": "remove_duplicates"}],
            "inline_content": "a,b\n1,x\n1,x\n2,y\n",
            "filename_hint": "t.csv",
        },
    )
    output = result.structured_content["output"]
    assert output["path"] is None
    assert output["content"] == "a,b\n1,x\n2,y\n"
    assert (output["format"], output["rows"]) == ("csv", 2)


async def test_clean_dataset_errors_reach_the_agent(client, messy_copy: Path):
    step = {"action": "fix_type", "column": "nope", "target_type": "numeric"}
    result = await client.call_tool(
        "clean_dataset", {"steps": [step], "file_path": str(messy_copy)}
    )
    assert result.is_error is True
    assert "Column 'nope' doesn't exist" in result.content[0].text
    assert [p.name for p in messy_copy.parent.iterdir()] == ["messy_sample.csv"]


async def test_clean_dataset_schema_rejects_json_null_keep(client, messy_copy: Path):
    result = await client.call_tool(
        "clean_dataset",
        {
            "steps": [{"action": "remove_duplicates", "keep": None}],
            "file_path": str(messy_copy),
        },
    )
    assert result.is_error is True  # "none" is a string, not JSON null
    assert [p.name for p in messy_copy.parent.iterdir()] == ["messy_sample.csv"]


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
    assert "clean_dataset" in text


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
