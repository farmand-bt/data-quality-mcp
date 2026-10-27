# data-quality-mcp

An MCP server that exposes the core logic of the Data Quality Analyzer project as tools any MCP-compatible AI agent can call - Claude Desktop, Cursor, claude.ai, or a custom agent.

Instead of a person uploading a CSV to a dashboard, an agent calls `profile_dataset` as part of its own workflow and gets back a structured, schema-validated quality report. The server makes **no LLM calls**: it is deterministic tooling, and the reasoning happens in whichever agent calls it.

> **Status:** Milestones 1–3.5 of 5. Profiling covers types, statistics, missing values and what they go with, outliers and possible placeholder codes, duplicates and repeated IDs, correlations, prioritized recommendations and a quality score. `clean_dataset` applies cleaning steps and reports what changed. Every tool takes a file path or the data inline. Remote (HTTP) deployment comes in Milestone 4. See [`planning/project-overview.md`](planning/project-overview.md) and the per-milestone specs in [`planning/`](planning/).

## Tools

| Tool | What it does |
|---|---|
| `profile_dataset` | Full report for a `.csv` or `.xlsx` dataset: quality score and grade, recommendations sorted by severity, and per column the inferred type, type mismatches, missing values (and which other columns they go with), stats, outliers and warnings, plus duplicate rows and correlations. |
| `detect_outliers` | Outliers in every numeric column (IQR primary, z-score secondary), with fences, row indices and possible placeholder codes (e.g. -1000, 9999999). |
| `find_duplicates` | Exact and near-duplicate rows, and ID columns whose values appear on rows with different data, with row indices. |
| `analyze_correlations` | Pearson (numeric) and Cramér's V (categorical) associations, plus highly correlated pairs. |
| `clean_dataset` | Applies a list of cleaning steps (missing values, duplicates, type fixes, outliers), writes the result to a **new** file or returns it inline, and reports a log plus the quality score before and after. |

The four profiling tools are read-only. `clean_dataset` never changes its input and never overwrites a file. Everything is deterministic. Row indices are 0-based data-row positions (CSV line = index + 2), and lists are capped at 50 entries.

### Input: a file path or the data itself

Every tool takes either `file_path`, an absolute path on the machine running the server, or `inline_content` plus `filename_hint`:
- **CSV:** send it as plain text.
- **XLSX:** send it base64-encoded. `filename_hint` (e.g. `sales.csv` or `report.xlsx`) sets the format and becomes the dataset's name.

Inline data is for small datasets: every character passes through the conversation. The limits are 50 MB, 500,000 rows, and for XLSX 200 MB unpacked. On Windows, network paths (`\\server\share\...`) are rejected, because opening one connects to that host and can leak your login credentials.

### Cleaning

`clean_dataset` takes the steps a profile suggests, for example:

```json
[
  {"action": "remove_duplicates", "keep": "first"},
  {"action": "fix_type", "column": "sqft", "target_type": "numeric"},
  {"action": "handle_missing", "column": "price", "strategy": "fill_median"},
  {"action": "handle_outliers", "column": "price", "strategy": "remove"}
]
```

The steps and their order are those of the Data Quality Analyzer's cleaning panel:
- Type fixes and fills run first, then outlier removal, then row and column drops. That way, outlier rows are exactly the ones `detect_outliers` reported.
- The whole list is validated before anything is written.

Where the result goes depends on the input:
- **File input:** a new `<name>_cleaned.<ext>` is written next to the original (`_cleaned_2`, … if taken), or to `output_path`.
- **Inline input:** the cleaned data comes back in the result, up to 1 MB.

The result's `after` figures come from profiling the written output. Dates converted with `fix_type` are written to a CSV as ISO text, which counts as typed, so CSV and `.xlsx` output score the same.

Placeholder codes that `detect_outliers` lists under `possible_sentinels` are only suspects. Once the user confirms them, they can be set to missing without touching genuine extremes:

```json
{"action": "handle_outliers", "column": "price", "strategy": "replace_nan",
 "method": "sentinel", "values": [-1000, 9999999]}
```

**Trust:** the server runs with your permissions, so `file_path` and `output_path` can reach anything you can. That's normal for a local stdio server, but be careful with data from untrusted sources: text in the data could steer an agent. A cleaned file isn't sanitized for spreadsheets either. Cells starting with `=`, `+`, `-` or `@` stay live formulas when the file is opened in Excel.

## Resources & prompts

| Kind | Name | What it is |
|---|---|---|
| Resource | `dataquality://schema/report` | JSON Schema of the full report |
| Resource | `dataquality://docs/quality-score` | How the score, grade and outlier methods work |
| Prompt | `review_data_quality(file_path)` | Runs a full review and proposes an ordered cleaning plan |

## Setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/). `make` is optional; on Windows, install it with `winget install ezwinports.make`.

```bash
git clone https://github.com/farmand-bt/data-quality-mcp.git
cd data-quality-mcp
make install        # or: uv sync
make test           # or: uv run pytest --cov
```

## Connect a client

The server speaks MCP over stdio, so the client launches it. Always use **absolute paths**: the client starts the server from an arbitrary working directory.

**Claude Desktop:** give the client its **own installation**, so it never locks your development environment:

```bash
uv tool install --editable .     # once; installs data-quality-mcp.exe into ~/.local/bin
```

Then add an entry to `claude_desktop_config.json` (**Settings → Developer → Edit Config** opens the right file; the Microsoft Store version keeps it under `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`). Merge it into any existing `mcpServers`, and fully quit and reopen Claude Desktop afterwards:

```json
{
  "mcpServers": {
    "data-quality": {
      "command": "C:\\Users\\YOU\\.local\\bin\\data-quality-mcp.exe",
      "args": []
    }
  }
}
```

The install is **editable**: it runs the code you have checked out, and code changes apply the next time the client starts the server. Only when *dependencies* change, quit the client and run `uv tool install --editable . --reinstall`.

> **Why not point the client at `.venv\Scripts\data-quality-mcp.exe`?** That works, but on Windows a running server locks that file, so `uv sync` / `make install` fail with "file in use" while the client is open. A separate tool install avoids this.

On macOS/Linux the command is `~/.local/bin/data-quality-mcp` (use the absolute path). Alternatively, have uv launch the project directly: set `"command"` to the absolute path of `uv` (from `where uv` or `which uv`) and `"args"` to `["--directory", "/path/to/data-quality-mcp", "run", "data-quality-mcp"]`.

**Cursor:** use the same entry in `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global), adding `"type": "stdio"`.

Then ask the agent something like *"Profile C:\data\sales.csv and tell me what needs cleaning."*, and once you agree with its plan, *"Apply it."*

## Development

```bash
make dev            # MCP Inspector in the browser (needs Node.js)
make lint           # ruff check + format check (what CI runs)
make format         # auto-fix + format
make generate-data  # regenerate synthetic fixtures
```

Architecture and design rules are in [`docs/architecture.md`](docs/architecture.md) and [`CLAUDE.md`](CLAUDE.md). The key rule: the engines (`profiler_core/`, `cleaner_core/`) never import the MCP SDK. Only `tools/` and `server.py` do, and a test enforces this.

Every change goes through a pull request. CI runs commitlint on the PR title, lint, tests (Ubuntu and Windows, Python 3.10–3.14), build, security scans (pip-audit, Bandit, CodeQL) and an automated Claude review.

## License

MIT
