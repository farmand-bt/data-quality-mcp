# Data Quality MCP Server — Project Plan

## Project Overview

An MCP (Model Context Protocol) server that exposes the core logic of the Data Quality Analyzer project as tools any MCP-compatible AI agent can call — Claude Desktop, Cursor, claude.ai, or a custom agent. Instead of a human uploading a CSV to a Streamlit dashboard, an agent can call `profile_dataset("sales.csv")` directly as part of its own workflow, get back a structured quality report, and optionally request cleaning actions.

**Goal:** A focused, protocol-level portfolio project that demonstrates understanding of MCP itself — not just calling an LLM API, but building the kind of tool-serving infrastructure that agentic systems are increasingly built on. This is intentionally leaner in scope than the previous two projects: there is no UI to build, and no LLM dependency at all — the "intelligence" here is whichever agent calls the server, not the server itself.

**How this connects to your other projects:** The profiling and cleaning logic is adapted and simplified from your Data Quality Analyzer. This server can later become a tool your multi-agent orchestrator project calls.

---

## Tech Stack

| Component              | Tool / Library                          | Why                                                                 |
|------------------------|------------------------------------------|----------------------------------------------------------------------|
| Language               | Python 3.10+                            | Required by the current MCP Python SDK                              |
| MCP SDK                | `mcp` (official Python SDK, FastMCP API) | Official SDK — bundles the high-level FastMCP decorator interface   |
| Data manipulation      | `pandas`                                 | Same as the original Data Quality Analyzer                          |
| Statistical analysis   | `scipy`, `numpy`                         | Outlier detection, distributions, correlations                      |
| File handling          | `openpyxl`                               | Excel (.xlsx) support                                                |
| Packaging              | `pyproject.toml`, `src/` layout          | Modern, installable Python package layout                           |
| Code quality           | `ruff`                                   | Linter + formatter                                                  |
| Testing                | `pytest`                                 | Unit tests for tools and core logic                                 |
| Debugging (dev only)   | MCP Inspector (bundled with the SDK)     | Interactive tool for testing tools/resources/prompts before wiring to a real client |
| Transport (Milestone 4)| Streamable HTTP (via SDK)                | Lets the server run remotely, not just locally over stdio           |
| Containerization (M4)  | Docker                                   | Standard way to deploy an MCP server remotely                       |

**Total cost: $0.** This project makes no LLM API calls at all — it's pure deterministic tooling. No GWDG quota is used here.

**Important — verify before building:** MCP tooling moves fast. The exact `mcp` package version, the current FastMCP decorator syntax, and the current Claude Desktop / Cursor config file formats should all be checked against live documentation before writing code, not assumed from this document. This is called out explicitly as the first task in Milestone 1.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                     MCP Client (any agent)                     │
│        Claude Desktop · Cursor · claude.ai · custom agent      │
└─────────────────────────────┬───────────────────────────────────┘
                               │  MCP protocol (JSON-RPC)
                               │  stdio (local) or Streamable HTTP (remote, M4)
                               ▼
┌───────────────────────────────────────────────────────────────┐
│                    data-quality-mcp-server                      │
│                                                                 │
│   ┌───────────────┐   ┌────────────────┐   ┌─────────────────┐ │
│   │    Tools      │   │   Resources    │   │     Prompts     │ │
│   │ profile_...   │   │  report://...  │   │ summarize-...   │ │
│   │ detect_...    │   │  (last report) │   │                 │ │
│   │ clean_...     │   └────────────────┘   └─────────────────┘ │
│   └───────┬───────┘                                             │
│           │  thin adapter layer (parses args, calls core,       │
│           │  shapes the JSON response)                          │
│           ▼                                                     │
│   ┌─────────────────────┐      ┌─────────────────────┐         │
│   │   profiler_core/     │      │   cleaner_core/      │         │
│   │   (pure Python,      │      │   (pure Python,      │         │
│   │    no MCP imports —  │      │    no MCP imports —  │         │
│   │    transport-        │      │    transport-        │         │
│   │    agnostic)          │      │    agnostic)          │         │
│   └─────────────────────┘      └─────────────────────┘         │
└───────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
data-quality-mcp-server/
│
├── README.md                        # What MCP is, how to connect, tool reference
├── pyproject.toml                   # Dependencies, src layout, entry point
├── .env.example                     # Only needed once remote auth is added (Milestone 4)
├── .gitignore
├── Makefile
│
├── src/
│   └── data_quality_mcp/
│       ├── __init__.py
│       ├── server.py                # FastMCP app instance, registers tools/resources/prompts
│       ├── config.py                # Settings: size limits, thresholds
│       │
│       ├── profiler_core/           # Vendored + simplified profiling engine (pure Python)
│       │   ├── __init__.py
│       │   ├── loader.py            # Loads from file_path OR inline_content
│       │   ├── type_detector.py
│       │   ├── stats.py
│       │   ├── missing.py
│       │   ├── outliers.py          # Added Milestone 2
│       │   ├── duplicates.py        # Added Milestone 2
│       │   ├── correlations.py      # Added Milestone 2
│       │   ├── recommendations.py   # Added Milestone 2
│       │   └── report.py            # Aggregates everything into the unified schema
│       │
│       ├── cleaner_core/            # Vendored + simplified cleaning engine (added Milestone 3)
│       │   ├── __init__.py
│       │   ├── missing_handler.py
│       │   ├── duplicate_handler.py
│       │   ├── type_fixer.py
│       │   ├── outlier_handler.py
│       │   └── pipeline.py
│       │
│       └── tools/                   # Thin MCP-facing layer — the only files that import `mcp`
│           ├── __init__.py
│           ├── profiling_tools.py
│           ├── cleaning_tools.py    # Added Milestone 3
│           ├── resources.py         # Added Milestone 2
│           └── prompts.py           # Added Milestone 2
│
├── tests/
│   ├── test_loader.py
│   ├── test_profiler_core.py
│   ├── test_cleaner_core.py
│   ├── test_tools.py
│   └── fixtures/
│       ├── clean_sample.csv
│       └── messy_sample.csv
│
├── examples/
│   ├── claude_desktop_config.json   # Example client config for local stdio connection
│   └── sample_datasets/
│       └── messy_data.csv
│
├── scripts/
│   └── generate_messy_data.py
│
├── Dockerfile                       # Added Milestone 4
│
└── docs/
    └── architecture.md
```

---

## Milestone Roadmap (Summary)

This project is built across 5 milestones — deliberately fewer and lighter than your previous two projects, since there's no UI layer here. **Milestone 1 is detailed in full below.** Subsequent milestones will be provided one at a time.

| Milestone | Focus                                | What it adds                                                                 |
|-----------|---------------------------------------|-------------------------------------------------------------------------------|
| **1**     | **Scaffolding + Core Profiling**      | Repo setup, `profiler_core` (types/stats/missing), first tool (`profile_dataset`) over stdio |
| **2**     | **Expanded Tool Surface**             | Outliers, duplicates, correlations, recommendations tools; a resource; a prompt |
| **3**     | **Flexible Input + Cleaning Tools**   | Inline data support (not just file paths), cleaning tools ported from the cleaner engine |
| **4**     | **Remote Deployment**                 | Streamable HTTP transport, Docker, minimal auth, a live deployment            |
| **5**     | **Polish & Docs**                     | README with connection guides, full test pass, optional MCP registry listing  |

**Important for scaffolding decisions made now:**
- `profiler_core/` and `cleaner_core/` must never import anything from `mcp`. They should be plain, testable Python that could theoretically be reused outside an MCP context. The `tools/` layer is the only place that touches the MCP SDK — this keeps the core logic transport-agnostic ahead of Milestone 4, when a second transport (HTTP) is added alongside stdio.
- `profiler_core/loader.py` should be designed from Milestone 1 to accept either a `file_path` or `inline_content` (even though only `file_path` is wired to a tool until Milestone 3). Retrofitting this later would mean touching every tool signature.
- Every tool's docstring and type hints matter more here than in a typical project — FastMCP derives the schema and description an agent sees directly from them. Write them as if a language model, not a human, is the only reader.

---

## Key Design Decisions

### Tool Output Schema
Every profiling tool returns JSON matching this shape (fields not yet computed in Milestone 1 are present but null/empty, not missing):

```python
{
    "dataset": {
        "name": "sales.csv",
        "source": "file_path",       # or "inline_content" (from Milestone 3)
        "rows": 1000,
        "columns": 8,
        "quality_score": 82,          # 0–100 composite score
        "quality_grade": "B"          # A/B/C/D/F
    },
    "columns": {
        "<column_name>": {
            "pandas_dtype": "object",
            "inferred_type": "categorical",  # numeric, categorical, datetime, boolean, text, mixed
            "type_mismatch": False,
            "missing_count": 12,
            "missing_pct": 1.2,
            "stats": { },              # contents vary by inferred_type
            "outliers": {"iqr_count": 0, "zscore_count": 0, "iqr_indices": [], "zscore_indices": []},
            "warnings": []
        }
    },
    "duplicates": {"exact_count": 0, "exact_pct": 0.0, "sample_indices": []},
    "correlations": {"pearson": {}, "high_correlation_pairs": []},
    "recommendations": [],
    "generated_at": "2026-01-01T00:00:00"
}
```

### Dual Input Mode
Tools need to work both when the server runs locally over stdio (where reading a file path on disk is natural) and when it's deployed remotely over HTTP (where the server has no access to the caller's filesystem, so the caller must send the data itself). `loader.py` should accept `file_path: str | None` and `inline_content: str | None` (base64-encoded) from the start, even though the inline path isn't exercised by a real tool until Milestone 3.

### No LLM Dependency
This server does not call an LLM at any point. It's intentionally pure, deterministic tooling — the reasoning happens in whatever agent calls it. This is a deliberate contrast to your other two projects and worth calling out explicitly in the README: not every AI-adjacent project needs to call a model directly.

### Non-Destructive, Logged Cleaning
Carried over from the Data Quality Analyzer: cleaning tools (Milestone 3) never mutate in place, and every transformation is logged with what was done, to which column, and how many rows were affected.

### Resource Limits
Since this server may eventually be reachable over the internet (Milestone 4), size and row limits belong in `config.py` from the start — e.g., a max file size in MB and a max row count — even though there's no remote exposure yet to actually abuse them.

---

## Quick Reference Commands

```bash
# Setup
git clone <repo-url> && cd data-quality-mcp-server
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Run the server (stdio transport)
python -m data_quality_mcp.server

# Run with the MCP Inspector for interactive testing
# (verify the exact current command against the SDK's own docs — this has changed before)
mcp dev src/data_quality_mcp/server.py

# Lint & format
ruff check --fix .
ruff format .

# Run tests
pytest tests/

# Generate a messy test dataset
python scripts/generate_messy_data.py
```

---

## Milestone 1: Project Scaffolding + Core Profiling + First Tool (IMPLEMENT NOW)

**Goal:** A working MCP server, connectable via stdio, exposing a single `profile_dataset` tool that returns a real quality report.

**Tasks:**

0. **Before writing any code:** use plan mode to research current conventions rather than relying on training data, which may be stale for a fast-moving protocol like this one. Specifically look up:
   - The current official MCP Python SDK: exact package name and version, and the current `FastMCP` decorator syntax for tools (`@mcp.tool()`), resources, and prompts (check https://modelcontextprotocol.io/docs and the SDK's GitHub repo)
   - The current recommended way to run/debug a server locally (the MCP Inspector command may not be exactly `mcp dev <file>` anymore)
   - The current Claude Desktop and Cursor MCP config file formats, so `examples/claude_desktop_config.json` is accurate
   
   Propose a scaffolding plan based on what's actually current, then proceed.

1. Initialize git repo, `.gitignore` (`.env`, `__pycache__/`, `*.pyc`, `.venv/`, `data/`)
2. Create `pyproject.toml` with a `src/` layout, dependencies:
   - `mcp` (version confirmed in step 0)
   - `pandas`, `numpy`, `scipy`
   - `openpyxl`
   - `ruff`, `pytest` (dev dependencies)
3. Create `src/data_quality_mcp/config.py`:
   - `MAX_FILE_SIZE_MB = 50`
   - `MAX_ROWS = 500_000`
   - `MISSING_THRESHOLD = 0.30`
   - `OUTLIER_IQR_MULTIPLIER = 1.5`
   - `OUTLIER_ZSCORE_THRESHOLD = 3.0`
   - `HIGH_CORRELATION_THRESHOLD = 0.9`
   - `QUALITY_GRADE_THRESHOLDS = {"A": 90, "B": 80, "C": 70, "D": 60}`
4. Create `src/data_quality_mcp/profiler_core/loader.py`:
   - `load_data(file_path: str | None = None, inline_content: str | None = None, filename_hint: str | None = None) -> pd.DataFrame`
   - Supports `.csv` and `.xlsx`
   - Raises a clear error if neither or both inputs are given
   - Enforces `MAX_FILE_SIZE_MB` and `MAX_ROWS` from config
5. Create `src/data_quality_mcp/profiler_core/type_detector.py`, `stats.py`, `missing.py` — ported and simplified from the Data Quality Analyzer's equivalents (numeric/categorical/datetime/boolean/text/mixed detection; per-column stats; missing value counts and percentages)
6. Create `src/data_quality_mcp/profiler_core/report.py`:
   - `generate_report(df: pd.DataFrame, name: str, source: str) -> dict` matching the schema in Key Design Decisions
   - `duplicates`, `correlations`, and `recommendations` fields present but empty until Milestone 2
7. Create `src/data_quality_mcp/tools/profiling_tools.py`:
   - `profile_dataset(file_path: str) -> dict` — a thin wrapper: calls `loader.load_data`, then `report.generate_report`, returns the dict
   - Write a deliberately clear, complete docstring — this is what an agent will read to decide when and how to call the tool
8. Create `src/data_quality_mcp/server.py`:
   - Instantiate the FastMCP app
   - Register the `profile_dataset` tool
   - Entry point that runs over stdio transport
9. Create `examples/claude_desktop_config.json` with a working local stdio config (verify format per step 0)
10. Create `scripts/generate_messy_data.py`: a synthetic ~500-row dataset with missing values, mixed types, and a couple of outliers — save to `tests/fixtures/messy_sample.csv` and `examples/sample_datasets/messy_data.csv`
11. Create `Makefile` with: `install`, `run`, `dev` (Inspector), `lint`, `test`
12. Write `tests/test_loader.py` and `tests/test_profiler_core.py` against the messy fixture
13. Verify end-to-end: run the server via the MCP Inspector (or connect it to Claude Desktop using the example config) and confirm `profile_dataset` is discoverable and returns a correct, schema-matching report on the messy sample dataset

**Commit message:** `feat: MCP server scaffolding with core profiling and profile_dataset tool`
