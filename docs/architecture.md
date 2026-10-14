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

`profiler_core/schema.py` defines the report as TypedDicts (`DatasetReport`, `ColumnReport`, ...) and the focused tools' results (`OutlierResult`, `DuplicateResult`, `CorrelationResult`). Each tool's return annotation is one of them, so the SDK publishes the contract to clients as the tool's **output schema** and validates every result against it before sending. The same schema is also served as the resource `dataquality://schema/report`.

- These TypedDicts use `typing_extensions.TypedDict`, because Pydantic, which the SDK uses for schemas, rejects `typing.TypedDict` on Python < 3.12.
- Since Milestone 2 every section is computed. A column's `outliers` is `null` only when detection doesn't apply (non-numeric columns, or fewer than 4 numeric values). `null` never stands in for "checked, none found"; that is `0`.
- **Row indices** are 0-based positions of data rows (header excluded): index `i` is line `i + 2` of a CSV file. `report.py` resets the index before analysis to guarantee this. Every index list is capped at `config.MAX_REPORTED_INDICES` (50), and `indices_truncated` says when a list was cut. Counts and percentages are always exact. A 500k-row dataset produces a report of about 10 KB.

## Consistency with the Data Quality Analyzer

The outlier, duplicate, correlation and recommendation logic is ported from `llm-data-quality-analyzer` with the same methods, thresholds and rules, so both projects give the same answers on the same data. The ported test cases live in the `TestAnalyzerParity` classes. Every deviation, with its reason, is listed in [`planning/milestone-2.md`](../planning/milestone-2.md#deviations-from-the-analyzer).

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

## Quality score

The score starts at 100 and loses weighted, capped penalties. The result is clamped to 0–100 and rounded. The components are the same as the analyzer's. The text served as `dataquality://docs/quality-score` is generated from the same constants (`profiler_core/scoring.py`), so it can't drift from the code.

| Component | Penalty | Cap | Since |
|---|---|---|---|
| Completeness | 2 × overall missing-cell % | 40 | Milestone 1 |
| Type consistency | 20 × (columns with `type_mismatch`) / (all columns) | 20 | Milestone 1 |
| Duplicates | 1 × exact-duplicate-row % | 10 | Milestone 2 |
| Outliers | 2 × average IQR outlier % across numeric columns that have outliers | 10 | Milestone 2 |

Grades come from `config.QUALITY_GRADE_THRESHOLDS`: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, otherwise F.

The analyzer's "+5 clean-column bonus" was dropped in Milestone 1, because it could partly cancel out real problems. **Scores dropped in Milestone 2** when the duplicate and outlier penalties started counting. The messy sample went from 84 (B) to **77 (C)**: 100 − 9.54 (missing) − 6 (types) − 3.65 (duplicates) − 4.23 (outliers) = 76.58.

`type_mismatch` means the stored type differs from the semantic type: numbers stored as text (`numeric`/`mixed`), dates stored as text (`datetime`), or boolean-like strings (`boolean`). Inconsistent category casing (`"Male"` vs `"male"`) is reported as a warning and a `consistency` recommendation, not as a type mismatch, so it doesn't affect the score.

## Outliers, duplicates, correlations

- **Outliers**, per numeric or mixed column:
  - **IQR (primary):** values outside the fences `[Q1 − 1.5 × IQR, Q3 + 1.5 × IQR]`. The fences are reported as `iqr_lower_bound` / `iqr_upper_bound`. IQR alone drives the score, the recommendations and the warnings.
  - **Classic z-score (secondary):** `|x − mean| / std > 3`. Extreme values inflate the std and can mask other outliers. On the messy sample, z flags 2 price outliers where IQR flags 15. It is kept for parity with the analyzer, and the tool docstrings tell agents to prefer IQR.
- **Duplicates:**
  - exact duplicates: `exact_count` is how many rows deduplication would remove
  - near-duplicates: rows identical on all columns but one, excluding exact duplicates, skipped above 50 columns. They're reported but not scored. This is the most expensive step: one full pass per column, about 25 s of the roughly 29 s full report at the limits (500k rows × 50 columns). The column limit is the analyzer's own, kept for parity.
- **Correlations:** Pearson over the first 50 numeric columns, and Cramér's V (`scipy` χ², no bias correction) over the first 15 categorical/boolean columns with at most 50 distinct values. Pairs with |value| ≥ 0.9 are listed strongest first.

## MCP surface

| Kind | Name | Purpose |
|---|---|---|
| Tool | `profile_dataset` | Full report, including recommendations and score |
| Tool | `detect_outliers` / `find_duplicates` / `analyze_correlations` | One section each, plus `dataset` basics. Smaller results for narrow questions |
| Resource | `dataquality://schema/report` | JSON Schema of the full report (`application/schema+json`) |
| Resource | `dataquality://docs/quality-score` | How the score, grade and outlier methods work (`text/markdown`) |
| Prompt | `review_data_quality(file_path)` | Guides an agent through a full review and an ordered cleaning plan |

The resources are **stateless**: there's no "last report" cache. Server-side state would leak one caller's data to another once several HTTP clients connect (Milestone 4). All tools are annotated read-only and closed-world.

## Compatibility

- Python 3.10–3.14. pandas 3 requires Python ≥ 3.11, so Python 3.10 runs pandas 2. String columns report `pandas_dtype` as `object` on pandas 2 and `str` on pandas 3. CI tests both lines.
- MCP Python SDK v2 (`mcp>=2.2,<3`). v2 renamed v1's `FastMCP` to `MCPServer`.
