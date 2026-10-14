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
| MCP SDK                | `mcp[cli]` v2 (official Python SDK, `MCPServer` API) | Official SDK. v2 renamed v1's `FastMCP` to `MCPServer` (`from mcp.server import MCPServer`); decorators are unchanged |
| Data manipulation      | `pandas`                                 | Same as the original Data Quality Analyzer                          |
| Statistical analysis   | `scipy`, `numpy`                         | Outlier detection, distributions, correlations                      |
| File handling          | `openpyxl`                               | Excel (.xlsx) support                                                |
| Packaging              | `pyproject.toml`, `src/` layout          | Modern, installable Python package layout                           |
| Code quality           | `ruff`                                   | Linter + formatter                                                  |
| Testing                | `pytest`, `pytest-cov`, anyio plugin     | Unit tests for core logic; in-memory `mcp.Client` tests for tools   |
| Environment / tasks    | `uv`, GNU `make`                         | `uv sync` manages `.venv` + `uv.lock`; `make` wraps common commands |
| Debugging (dev only)   | MCP Inspector (bundled with the SDK)     | Interactive tool for testing tools/resources/prompts before wiring to a real client |
| Transport (Milestone 4)| Streamable HTTP (via SDK)                | Lets the server run remotely, not just locally over stdio           |
| Containerization (M4)  | Docker                                   | Standard way to deploy an MCP server remotely                       |
| CI/CD                  | GitHub Actions + Dependabot              | Free for public repos; every change goes through a PR gated by checks |
| AI code review         | `anthropics/claude-code-action`          | Runs on PRs, authenticated with the Claude Pro subscription (OAuth token, no API key) |

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
│   │ profile_...   │   │ dataquality:// │   │ review_data_... │ │
│   │ detect_...    │   │ (schema, docs) │   │                 │ │
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
data-quality-mcp/
│
├── CLAUDE.md                        # Standing invariants for every Claude Code session
├── .github/
│   ├── dependabot.yml               # Weekly pip + GitHub Actions updates
│   └── workflows/
│       ├── commitlint.yml           # PR title follows Conventional Commits
│       ├── lint.yml                 # ruff check + ruff format --check
│       ├── test.yml                 # pytest matrix (Ubuntu + Windows × Python 3.10–3.13)
│       ├── build.yml                # wheel/sdist build + twine check (Docker build added M4)
│       ├── security.yml             # pip-audit, Bandit, CodeQL
│       └── claude-review.yml        # Claude Code Review on PRs
├── README.md                        # What MCP is, how to connect, tool reference
├── pyproject.toml                   # Dependencies, src layout, entry point
├── .env.example                     # Only needed once remote auth is added (Milestone 4)
├── .gitignore
├── Makefile
│
├── src/
│   └── data_quality_mcp/
│       ├── __init__.py
│       ├── server.py                # Creates the MCPServer app, calls each tools module's register(app), runs it
│       ├── config.py                # Settings: size limits, thresholds (no mcp imports)
│       │
│       ├── profiler_core/           # Vendored + simplified profiling engine (pure Python)
│       │   ├── __init__.py
│       │   ├── exceptions.py        # Domain errors (DataQualityError base, FileTooLargeError, ...)
│       │   ├── loader.py            # Loads from file_path OR inline_content
│       │   ├── type_detector.py
│       │   ├── stats.py
│       │   ├── missing.py
│       │   ├── schema.py            # Report contract as TypedDicts (source of the tool's output schema)
│       │   ├── json_safe.py         # numpy/pandas values -> strict-JSON-safe Python values
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
│       └── tools/                   # Thin MCP-facing layer — with server.py, the only code that imports `mcp`
│           ├── __init__.py
│           ├── profiling_tools.py   # Plain functions + register(app: MCPServer)
│           ├── cleaning_tools.py    # Added Milestone 3
│           ├── resources.py         # Added Milestone 2
│           └── prompts.py           # Added Milestone 2
│
├── tests/
│   ├── conftest.py
│   ├── test_architecture.py         # Fails if core packages import mcp, or if src/ calls print()
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

This project is built across 5 milestones — deliberately fewer and lighter than your previous two projects, since there's no UI layer here. Each milestone has its own spec in this folder (`planning/milestone-N.md`), written and reviewed as a `docs:` PR before implementation starts.

| Milestone | Focus                                | What it adds                                                                 |
|-----------|---------------------------------------|-------------------------------------------------------------------------------|
| **1** ✅  | **Scaffolding + Core Profiling** ([spec](milestone-1.md)) | Repo setup, `profiler_core` (types/stats/missing), first tool (`profile_dataset`) over stdio |
| **2** ✅  | **Expanded Tool Surface** ([spec](milestone-2.md)) | Outliers, duplicates, correlations, recommendations tools; a resource; a prompt |
| **3**     | **Flexible Input + Cleaning Tools**   | Inline data support (not just file paths), cleaning tools ported from the cleaner engine |
| **4**     | **Remote Deployment**                 | Streamable HTTP transport, Docker, minimal auth, a live deployment            |
| **5**     | **Polish & Docs**                     | README with connection guides, full test pass, optional MCP registry listing  |

**Important for scaffolding decisions made now:**
- **The core/tools boundary is the single most important structural decision in this project.** `profiler_core/`, `cleaner_core/`, and `config.py` must never import anything from `mcp`. They should be plain, testable Python that could be reused outside an MCP context. Only `tools/` and `server.py` touch the MCP SDK — this keeps the core logic transport-agnostic ahead of Milestone 4, when a second transport (HTTP) is added alongside stdio.
- **Registration pattern (avoids circular imports and keeps the boundary clean):** each `tools/*.py` module defines plain functions plus a `register(app: MCPServer) -> None` function that registers them. `server.py` creates the app, calls each module's `register(app)`, and runs it. Tool modules never import a global app object from `server.py`.
- **The boundary is enforced by a test, not by review alone:** `tests/test_architecture.py` walks `profiler_core/` and `cleaner_core/` (and `config.py`) and fails on any `import mcp` / `from mcp`.
- `profiler_core/loader.py` should be designed from Milestone 1 to accept either a `file_path` or `inline_content` (even though only `file_path` is wired to a tool until Milestone 3). Retrofitting this later would mean touching every tool signature.
- Every tool's docstring and type hints matter more here than in a typical project — the SDK's `MCPServer` derives the schema and description an agent sees directly from them. Write them as if a language model, not a human, is the only reader.

---

## Key Design Decisions

### Tool Output Schema
`profile_dataset` returns JSON matching this shape. Since Milestone 2 every section is computed. A column's `outliers` is `null` only when detection doesn't apply (non-numeric columns, or fewer than 4 numeric values); an agent reads `0` as "checked, none found", so `null` is never used for that. Row indices are 0-based data-row positions (CSV line = index + 2), and every index list is capped at 50, with `indices_truncated`. The focused tools (`detect_outliers`, `find_duplicates`, `analyze_correlations`) return `{"dataset": {name, source, rows, columns}, "<section>": ...}` using the same section shapes. The authoritative definition is `src/data_quality_mcp/profiler_core/schema.py`, also served as the resource `dataquality://schema/report`.

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
            "inferred_type": "numeric",      # numeric, categorical, datetime, boolean, text, mixed
            "type_mismatch": False,
            "missing_count": 12,
            "missing_pct": 1.2,
            "stats": { },              # contents vary by inferred_type
            "outliers": {              # null for non-numeric columns or < 4 values
                "iqr_count": 15, "iqr_pct": 2.88,
                "iqr_lower_bound": 147204.5, "iqr_upper_bound": 546016.5,
                "iqr_indices": [3, 41],
                "zscore_count": 2, "zscore_pct": 0.38, "zscore_indices": [41, 77],
                "indices_truncated": False
            },
            "warnings": []
        }
    },
    "duplicates": {
        "exact_count": 19, "exact_pct": 3.65, "sample_indices": [5, 18],
        "near_duplicate_count": 2, "near_duplicate_pct": 0.38, "near_sample_indices": [7, 90],
        "indices_truncated": False
    },
    "correlations": {
        "pearson": {"a": {"a": 1.0, "b": 0.97}},
        "cramers_v": {"c": {"d": 0.12}},
        "high_correlation_pairs": [{"col_a": "a", "col_b": "b", "method": "pearson", "value": 0.97}]
    },
    "recommendations": [       # sorted critical -> warning -> info
        {"severity": "warning", "category": "duplicate", "column": None,
         "message": "19 exact duplicate rows detected (3.6%): consider deduplication ..."}
    ],
    "generated_at": "2026-01-01T00:00:00Z"   # UTC, ISO 8601
}
```

### JSON-Safe Output
pandas/numpy values (`np.int64`, `np.float64`, `np.bool_`, `NaN`, `NaT`, `pd.Timestamp`) are not JSON-serializable, or serialize incorrectly (`NaN` is not valid JSON). `report.py` is responsible for guaranteeing that everything it returns is native Python (`int`, `float`, `bool`, `str`, `None`, `list`, `dict`), with `NaN`/`NaT` → `None` and timestamps → ISO strings. A test asserts `json.dumps(report, allow_nan=False)` succeeds on the messy fixture.

### Error Handling
The core raises its own domain exceptions, defined in `profiler_core/exceptions.py` (base class `DataQualityError`; e.g. `UnsupportedFormatError`, `FileTooLargeError`, `TooManyRowsError`). The core never raises or constructs MCP-specific errors. The `tools/` layer catches domain exceptions and turns them into clear MCP tool errors whose messages tell the calling agent what went wrong and how to fix the call.

### Configurable Thresholds
`config.py` holds the defaults, but core functions take thresholds and limits as keyword parameters (e.g. `missing_threshold: float = config.MISSING_THRESHOLD`) instead of reading globals deep inside the logic. That way the HTTP deployment in Milestone 4 can override limits without touching the core.

### stdio Hygiene
Over stdio, stdout *is* the JSON-RPC channel — any stray `print()` corrupts the protocol and breaks the client connection, often with no obvious error. No `print()` anywhere in the package; all logging goes to stderr through the `logging` module.

### Quality Score
`quality_score` (0–100) and `quality_grade` must come from a deterministic, documented formula — not an ad-hoc one invented during implementation. In Milestone 1 only completeness (missing values) and type consistency (type mismatches) are available, so the formula should be a weighted-penalty model over those components, designed so later components (duplicates, outliers) can be added in Milestone 2 without changing its shape. The formula and weights were approved in Milestone 1, completed in Milestone 2, and are documented in [`docs/architecture.md`](../docs/architecture.md#quality-score) and served as `dataquality://docs/quality-score`. Grades map from score via `QUALITY_GRADE_THRESHOLDS`.

### Dual Input Mode
Tools need to work both when the server runs locally over stdio (where reading a file path on disk is natural) and when it's deployed remotely over HTTP (where the server has no access to the caller's filesystem, so the caller must send the data itself). `loader.py` should accept `file_path: str | None` and `inline_content: str | None` (base64-encoded) from the start, even though the inline path isn't exercised by a real tool until Milestone 3.

### No LLM Dependency
This server does not call an LLM at any point. It's intentionally pure, deterministic tooling — the reasoning happens in whatever agent calls it. This is a deliberate contrast to your other two projects and worth calling out explicitly in the README: not every AI-adjacent project needs to call a model directly.

### Non-Destructive, Logged Cleaning
Carried over from the Data Quality Analyzer: cleaning tools (Milestone 3) never mutate in place, and every transformation is logged with what was done, to which column, and how many rows were affected.

### PR-Gated Workflow and CI
Every change (feature, fix, refactor, docs) goes through a branch and a PR. The `main protection` ruleset requires a PR and the required CI checks (Lint, Test, Build, Security and Commitlint jobs), and blocks force pushes and branch deletion. The repo allows squash merge only and deletes merged branches automatically. The repository admin (the owner) is a bypass actor, so a deliberate direct push or force push is still possible. Claude sessions and Dependabot always go through PRs. Because the PR title becomes the squash commit message, commitlint checks the PR title. If the test matrix or a job name changes, the ruleset's required-check list must be updated too.

| Workflow            | Trigger                         | Checks                                                                 |
|---------------------|----------------------------------|-------------------------------------------------------------------------|
| `commitlint.yml`    | pull_request                     | PR title matches Conventional Commits                                  |
| `lint.yml`          | pull_request, push to `main`     | `ruff check`, `ruff format --check`                                    |
| `test.yml`          | pull_request, push to `main`     | `pytest` with coverage; Ubuntu × Python 3.10–3.14, Windows × 3.10 and 3.14 (covers pandas 2 and 3) |
| `build.yml`         | pull_request, push to `main`     | `uv build`, `twine check --strict`, installed-wheel smoke test; Docker build from M4 |
| `security.yml`      | pull_request, push to `main`, weekly | `pip-audit` (dependency CVEs), Bandit (code), CodeQL (Python)       |
| `claude-review.yml` | pull_request                     | `anthropics/claude-code-action` review, authenticated via a `CLAUDE_CODE_OAUTH_TOKEN` repo secret generated with `claude setup-token` (uses the Pro subscription, no API key). Advisory, not a required check, so a usage limit can't block merges. Can be disabled if usage runs high. |
| Dependabot          | weekly                           | `uv` and GitHub Actions version updates, one grouped PR each           |

Action versions are pinned to a specific release, and each workflow sets minimal `permissions:`.

### Resource Limits
Since this server may eventually be reachable over the internet (Milestone 4), size and row limits belong in `config.py` from the start — e.g., a max file size in MB and a max row count — even though there's no remote exposure yet to actually abuse them.

---

## Quick Reference Commands

> **Development platform is Windows 11.** Everything runs through `uv` (manages `.venv` and `uv.lock`) and GNU `make` (installed with `winget install ezwinports.make`). Client configs must use absolute paths (see `examples/claude_desktop_config.json`).

```bash
# Setup
git clone https://github.com/farmand-bt/data-quality-mcp.git && cd data-quality-mcp
make install            # = uv sync (creates .venv with dev dependencies)

# Run the server (stdio transport; normally an MCP client launches it)
make run                # = uv run data-quality-mcp

# Open the MCP Inspector (needs Node.js / npx)
make dev                # = uv run mcp dev src/data_quality_mcp/server.py:app

# Lint (what CI runs) / auto-fix and format
make lint
make format

# Run tests with coverage
make test               # = uv run pytest --cov

# Regenerate the synthetic test/example datasets
make generate-data
```
