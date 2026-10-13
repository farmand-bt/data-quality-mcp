# Milestone 1: Project Scaffolding + Core Profiling + First Tool

> **Status: complete.** Merged as PR #1 (`f957773`). Follow-ups: PR #4 (commitlint accepts Dependabot titles), `main` ruleset created.
> This file is the original spec, kept as the record of what was asked for. Where the implementation differs, see **As built** at the end.
> Shared context (architecture, schema, design decisions) is in [project-overview.md](project-overview.md).

**Goal:** A working MCP server, connectable via stdio, exposing a single `profile_dataset` tool that returns a real quality report.

**Tasks:**

0. **Before writing any code:** use plan mode to research current conventions rather than relying on training data, which may be stale for a fast-moving protocol like this one. Specifically look up:
   - The current official MCP Python SDK: exact package name and version, and the current `FastMCP` decorator syntax for tools (`@mcp.tool()`), resources, and prompts (check https://modelcontextprotocol.io/docs and the SDK's GitHub repo)
   - The current recommended way to run/debug a server locally (the MCP Inspector command may not be exactly `mcp dev <file>` anymore)
   - The current Claude Desktop and Cursor MCP config file formats, so `examples/claude_desktop_config.json` is accurate — **including the Windows form** (absolute path to the venv `python.exe` or `uv`)
   - The SDK's in-memory client/session testing utility, for `tests/test_tools.py`
   - Whether `make` is available on this Windows machine, and if not, what to use instead
   
   Propose a scaffolding plan based on what's actually current, then proceed. The plan must also include:
   - The proposed `quality_score` formula and weights (see Key Design Decisions → Quality Score)
   - How the `register(app)` pattern will look concretely in `server.py` and `tools/profiling_tools.py`

1. Git repo already exists; add `.gitignore` (`.env`, `__pycache__/`, `*.pyc`, `.venv/`, `data/`)
2. Create `pyproject.toml` with a `src/` layout, a console-script entry point for the server, and dependencies:
   - `mcp` (version confirmed in step 0; likely the `mcp[cli]` extra so the Inspector works)
   - `pandas`, `numpy`, `scipy`
   - `openpyxl`
   - `ruff`, `pytest` (dev dependencies)
3. Create `src/data_quality_mcp/config.py`:
   - `MAX_FILE_SIZE_MB = 50`
   - `MAX_ROWS = 500_000`
   - `MISSING_THRESHOLD = 0.30`
   - `HIGH_CARDINALITY_THRESHOLD = 0.5` (added during implementation; used by the type detector)
   - `OUTLIER_IQR_MULTIPLIER = 1.5`
   - `OUTLIER_ZSCORE_THRESHOLD = 3.0`
   - `HIGH_CORRELATION_THRESHOLD = 0.9`
   - `QUALITY_GRADE_THRESHOLDS = {"A": 90, "B": 80, "C": 70, "D": 60}`
4. Create `src/data_quality_mcp/profiler_core/loader.py`:
   - `load_data(file_path: str | None = None, inline_content: str | None = None, filename_hint: str | None = None) -> pd.DataFrame`
   - Supports `.csv` and `.xlsx`
   - Raises a clear domain error if neither or both inputs are given
   - Enforces `MAX_FILE_SIZE_MB` and `MAX_ROWS` (accepted as parameters, defaulting to config)
   - All errors are domain exceptions from `profiler_core/exceptions.py` (create this file in this step)
5. Create `src/data_quality_mcp/profiler_core/type_detector.py`, `stats.py`, `missing.py` — ported and simplified from the Data Quality Analyzer's equivalents (numeric/categorical/datetime/boolean/text/mixed detection; per-column stats; missing value counts and percentages). Thresholds are keyword parameters with config defaults.
6. Create `src/data_quality_mcp/profiler_core/report.py`:
   - `generate_report(df: pd.DataFrame, name: str, source: str) -> dict` matching the schema in Key Design Decisions
   - `outliers`, `duplicates`, and `correlations` are `null` and `recommendations` is `[]` until Milestone 2
   - Computes `quality_score` / `quality_grade` with the formula approved in step 0
   - Output is fully JSON-safe (native Python types, `NaN` → `None`, UTC ISO `generated_at`)
7. Create `src/data_quality_mcp/tools/profiling_tools.py`:
   - `profile_dataset(file_path: str) -> dict` — a thin wrapper: calls `loader.load_data`, then `report.generate_report`, returns the dict
   - Catches domain exceptions and turns them into clear MCP tool errors
   - `register(app: MCPServer) -> None` registers the tool on the app passed in
   - Write a deliberately clear, complete docstring — this is what an agent will read to decide when and how to call the tool
8. Create `src/data_quality_mcp/server.py`:
   - Instantiate the `MCPServer` app (v2 name for v1's FastMCP)
   - Call `profiling_tools.register(app)`
   - `main()` entry point (wired to the console script and `if __name__ == "__main__"`) that runs over stdio transport
   - Logging configured to stderr only — no `print()` anywhere
9. Create `examples/claude_desktop_config.json` with a working local stdio config (verify format per step 0; show the Windows absolute-path form)
10. Create `scripts/generate_messy_data.py`: a synthetic ~500-row dataset with missing values, mixed types, and a couple of outliers — save to `tests/fixtures/messy_sample.csv` and `examples/sample_datasets/messy_data.csv`. Use a fixed random seed so the fixture is reproducible.
11. Create `Makefile` (or the Windows-friendly alternative chosen in step 0) with: `install`, `run`, `dev` (Inspector), `lint`, `test`
12. Write tests against the messy fixture:
    - `tests/test_architecture.py` — fails if `profiler_core/`, `cleaner_core/`, or `config.py` import `mcp`
    - `tests/test_loader.py` — both/neither inputs, unsupported format, size and row limits, CSV and XLSX
    - `tests/test_profiler_core.py` — types, stats, missing values, schema shape, `json.dumps(report, allow_nan=False)` succeeds
    - `tests/test_tools.py` — via the SDK's in-memory client: `profile_dataset` is listed, returns a schema-matching report, and a bad path produces a clean tool error
13. Write a short `docs/architecture.md`: the layer boundary, the `register(app)` pattern, the error-handling rule, and the quality score formula
14. Verify end-to-end: run the server via the MCP Inspector (or connect it to Claude Desktop using the example config) and confirm `profile_dataset` is discoverable and returns a correct, schema-matching report on the messy sample dataset
15. Add CI (see Key Design Decisions → PR-Gated Workflow and CI): the six workflows under `.github/workflows/` plus `.github/dependabot.yml`. In step 0, check the current versions and inputs of each action (especially `anthropics/claude-code-action` and its OAuth-token auth) instead of relying on memory.
16. Do all of Milestone 1 on a branch (e.g. `feat/milestone-1`) and open a PR. This is the first PR the new workflows run on. Merge only once every check is green. After the merge, set up the branch ruleset on `main` in GitHub settings.

**PR title / squash commit message:** `feat: MCP server scaffolding with core profiling and profile_dataset tool`

---

## As built (differences from the spec above)

- **MCP SDK v2:** `FastMCP` no longer exists; the server uses `MCPServer` (`from mcp.server import MCPServer`), and `register(app)` calls `app.add_tool(...)`.
- **Additional core modules:** `profiler_core/schema.py` (the report contract as TypedDicts, published as the tool's output schema) and `profiler_core/json_safe.py`.
- **Config:** added `HIGH_CARDINALITY_THRESHOLD = 0.5`.
- **Type mismatch:** inconsistent category casing is a warning, not a type mismatch, so it doesn't affect the score.
- **Tests:** `test_architecture.py` also forbids `print()` in `src/`; `test_tools.py` includes a real stdio subprocess round-trip.
- **Tooling:** uv + GNU make (`winget install ezwinports.make`); ruff excludes `*.md`.
- **Score:** the analyzer's +5 clean-column bonus was dropped. The messy sample scores 84 (B).
