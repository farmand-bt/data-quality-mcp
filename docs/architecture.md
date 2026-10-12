# Architecture

## Layers

```
MCP client (Claude Desktop, Cursor, custom agent)
        │  JSON-RPC over stdio (Streamable HTTP from Milestone 4)
        ▼
server.py           create_server(): builds MCPServer, calls each tools module's register(app)
        │
tools/              thin adapters: validate args, call the core, map domain errors to ToolError
        │
profiler_core/      pure Python engine (pandas/numpy): loader, type detection, stats,
config.py           missing values, report, schema. Never imports `mcp`.
```

**The rule:** only `server.py` and `tools/` may import the MCP SDK. `profiler_core/`, `cleaner_core/` (Milestone 3) and `config.py` never do. `tests/test_architecture.py` parses every core file and fails the build if one imports `mcp`. Because of this, adding the HTTP transport in Milestone 4 means adding an entry point, not rewriting the engine.

## Registration pattern

Tool modules never import a global app. Each one exposes plain functions plus a `register` function:

```python
# tools/profiling_tools.py
def profile_dataset(file_path: Annotated[str, Field(description=...)]) -> DatasetReport: ...

def register(app: MCPServer) -> None:
    app.add_tool(profile_dataset, title="Profile dataset",
                 annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))

# server.py
def create_server() -> MCPServer:
    app = MCPServer("data-quality", version=__version__, instructions=INSTRUCTIONS)
    profiling_tools.register(app)
    return app
```

This avoids circular imports. It also lets tests build a fresh server with `create_server()` and talk to it through the SDK's in-memory `Client`.

## The report contract

`profiler_core/schema.py` defines the report as TypedDicts (`DatasetReport`, `ColumnReport`, ...). The tool's return annotation is `DatasetReport`, so the SDK publishes the contract to clients as the tool's **output schema** and validates every result against it before sending.

- These TypedDicts use `typing_extensions.TypedDict`, because Pydantic, which the SDK uses for schemas, rejects `typing.TypedDict` on Python < 3.12.
- A section a later milestone will compute (`duplicates`, `correlations`, per-column `outliers`) is present and `null` until then. `null` means "not computed", while `0` would mean "checked, none found".

## Errors

| Where | What | What the agent sees |
|---|---|---|
| core | raises a `DataQualityError` subclass (`profiler_core/exceptions.py`) with an actionable message | n/a |
| tools | catches `DataQualityError` and re-raises it as `ToolError(message)` | `is_error=true` with that message |
| anything else | unexpected exception (a bug) | `is_error=true` and a generic "Error executing tool …" message; the traceback goes to the server log (stderr) |

The core never builds MCP errors, and the tools layer never swallows exceptions it doesn't recognize.

## JSON safety and stdio

- `generate_report` passes its output through `json_safe.to_json_safe`. It converts numpy scalars to native types, NaN/inf/NaT/NA to `None` and timestamps to ISO strings, and turns keys into strings. A test asserts that `json.dumps(report, allow_nan=False)` succeeds.
- Over stdio, stdout *is* the protocol channel, so `src/` must never call `print()`, and a test enforces this. Logs go to stderr. A subprocess test spawns the real server and completes a tool call through it.

## Quality score (Milestone 1 formula)

The score starts at 100 and loses weighted, capped penalties. A component that hasn't been computed yet (passed as `None`) is skipped. The result is clamped to 0–100 and rounded.

| Component | Penalty | Cap | Computed from |
|---|---|---|---|
| Completeness | 2 × overall missing-cell % | 40 | Milestone 1 |
| Type consistency | 20 × (columns with `type_mismatch`) / (all columns) | 20 | Milestone 1 |
| Duplicates | 1 × exact-duplicate-row % | 10 | Milestone 2 |
| Outliers | 2 × average IQR-outlier % | 10 | Milestone 2 |

Grades come from `config.QUALITY_GRADE_THRESHOLDS`: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, otherwise F.

The formula is adapted from the Data Quality Analyzer, minus its "+5 clean-column bonus", which could partly cancel out real problems. **Expect scores to drop once Milestone 2 adds the duplicate and outlier penalties.** For example, the messy sample scores 84 (B) in Milestone 1 even though it contains duplicate rows and extreme prices that aren't checked yet.

`type_mismatch` means the stored type differs from the semantic type: numbers stored as text (`numeric`/`mixed`), dates stored as text (`datetime`), or boolean-like strings (`boolean`). Inconsistent category casing (`"Male"` vs `"male"`) is reported as a warning, not a type mismatch, so it doesn't affect the score.

## Compatibility

- Python 3.10–3.14. pandas 3 requires Python ≥ 3.11, so Python 3.10 runs pandas 2. String columns report `pandas_dtype` as `object` on pandas 2 and `str` on pandas 3. CI tests both lines.
- MCP Python SDK v2 (`mcp>=2.2,<3`). v2 renamed v1's `FastMCP` to `MCPServer`.
