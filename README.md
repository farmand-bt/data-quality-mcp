# data-quality-mcp

An MCP server that exposes the core logic of the Data Quality Analyzer project as tools any MCP-compatible AI agent can call - Claude Desktop, Cursor, claude.ai, or a custom agent.

Instead of a person uploading a CSV to a dashboard, an agent calls `profile_dataset` as part of its own workflow and gets back a structured, schema-validated quality report. The server makes **no LLM calls**: it is deterministic tooling, and the reasoning happens in whichever agent calls it.

> **Status:** Milestone 1 of 5. Profiling covers types, statistics, missing values and a quality score. Outliers, duplicates, correlations, cleaning tools and remote (HTTP) deployment come in later milestones. See [`data-quality-mcp-server-overview.md`](data-quality-mcp-server-overview.md).

## Tools

| Tool | What it does |
|---|---|
| `profile_dataset(file_path)` | Profiles a `.csv` or `.xlsx` file (absolute path). Returns a quality score and grade, plus each column's inferred type, type mismatches, missing values, stats and plain-language warnings. Read-only. |

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

**Claude Desktop:** edit `%APPDATA%\Claude\claude_desktop_config.json` on Windows, or `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS. See [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json):

```json
{
  "mcpServers": {
    "data-quality": {
      "command": "C:\\path\\to\\data-quality-mcp\\.venv\\Scripts\\data-quality-mcp.exe",
      "args": []
    }
  }
}
```

On macOS/Linux, the command is `/path/to/data-quality-mcp/.venv/bin/data-quality-mcp`. As an alternative to the venv executable, you can have uv launch it: set `"command"` to the absolute path of `uv` (from `where uv` or `which uv`) and `"args"` to `["--directory", "/path/to/data-quality-mcp", "run", "data-quality-mcp"]`.

**Cursor:** use the same entry in `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global), adding `"type": "stdio"`.

Then ask the agent something like *"Profile C:\data\sales.csv and tell me what needs cleaning."*

## Development

```bash
make dev            # MCP Inspector in the browser (needs Node.js)
make lint           # ruff check + format check (what CI runs)
make format         # auto-fix + format
make generate-data  # regenerate synthetic fixtures
```

Architecture and design rules are in [`docs/architecture.md`](docs/architecture.md) and [`CLAUDE.md`](CLAUDE.md). The key rule: the profiling engine (`profiler_core/`) never imports the MCP SDK. Only `tools/` and `server.py` do, and a test enforces this.

Every change goes through a pull request. CI runs commitlint on the PR title, lint, tests (Ubuntu and Windows, Python 3.10–3.14), build, security scans (pip-audit, Bandit, CodeQL) and an automated Claude review.

## License

MIT
