# data-quality-mcp

An MCP server that exposes the core logic of the Data Quality Analyzer project as tools any MCP-compatible AI agent can call - Claude Desktop, Cursor, claude.ai, or a custom agent.

Instead of a person uploading a CSV to a dashboard, an agent calls `profile_dataset` as part of its own workflow and gets back a structured, schema-validated quality report. The server makes **no LLM calls**: it is deterministic tooling, and the reasoning happens in whichever agent calls it.

> **Status:** Milestones 1–4.5 of 5. Profiling covers types, statistics, missing values and what they go with, outliers and possible placeholder codes, duplicates and repeated IDs, correlations, prioritized recommendations and a quality score. `clean_dataset` applies cleaning steps and reports what changed. Every tool takes a file path or the data inline. The server runs over stdio or over HTTP (with a bearer token or OAuth sign-in), ships as a Docker image, and runs live on a free host for its owner, usable from claude.ai and Claude Code ([Live deployment](#live-deployment)). See [`planning/project-overview.md`](planning/project-overview.md) and the per-milestone specs in [`planning/`](planning/).

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

Inline data is for small datasets: every character passes through the conversation. The limits are 50 MB, 500,000 rows, and for XLSX 200 MB unpacked (they can be changed, see [Limits](#limits)). On Windows, network paths (`\\server\share\...`) are rejected, because opening one connects to that host and can leak your login credentials. Over HTTP, `file_path` and `output_path` are refused: send the data inline (see [Run over HTTP](#run-over-http)).

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

**Trust:** over stdio the server runs with your permissions, so `file_path` and `output_path` can reach anything you can. That's normal for a local stdio server, but be careful with data from untrusted sources: text in the data could steer an agent. Over HTTP a caller can only send data and get results back, never read or write the server's files. A cleaned file isn't sanitized for spreadsheets either. Cells starting with `=`, `+`, `-` or `@` stay live formulas when the file is opened in Excel.

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

## Run over HTTP

`data-quality-mcp --transport http` serves [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) at `http://127.0.0.1:8000/mcp` (stateless, JSON responses). `--host` and `--port` change the address. `GET /health` answers `{"status": "ok"}` without a token.

Over HTTP the server uses exactly one of three auth modes, and refuses to start if two are set:

| Mode | Chosen by | For |
|---|---|---|
| Bearer token | `DATA_QUALITY_MCP_TOKEN` | Local use, Docker, CI, Claude Code or VS Code with a header |
| OAuth sign-in | `DATA_QUALITY_MCP_OAUTH_ISSUER` | A public deployment, for claude.ai and Claude Code ([Live deployment](#live-deployment)) |
| None | `--no-auth`, loopback only | Quick local experiments |

**The token** is required on every host unless OAuth is set. Every request must carry it as `Authorization: Bearer <token>`:

1. Copy `.env.example` to `.env` (it's gitignored) and set `DATA_QUALITY_MCP_TOKEN`. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. It needs at least 32 characters from `A-Z a-z 0-9 - . _ ~ + /`. Write it **unquoted**: `docker --env-file` would keep quotes as part of the value.
2. `make run-http` (or `uv run --env-file .env data-quality-mcp --transport http`).

Without a token the server refuses to start and says why. For a quick local experiment, `--no-auth` turns auth off, and only `127.0.0.1`, `localhost` or `::1` accept it. **Never use `--no-auth` behind a reverse proxy or a tunnel** (nginx, Caddy, cloudflared…): a proxy on the same machine forwards public traffic from a loopback address, so the server would be open to anyone who can reach the proxy.

**Clients:**
- **Claude Code:** `claude mcp add --transport http data-quality http://localhost:8000/mcp --header "Authorization: Bearer <token>"`
- **VS Code:** [`examples/vscode_mcp.json`](examples/vscode_mcp.json), for `.vscode/mcp.json`. It prompts for the token and stores it securely, so the file holds no secret.
- **MCP Inspector (v2):** `npx @modelcontextprotocol/inspector --cli --transport http --server-url http://localhost:8000/mcp --header "Authorization: Bearer <token>" --method tools/list`, or the config file [`examples/inspector_config.json`](examples/inspector_config.json) (`--config ... --server data-quality-http`). That file holds the token as written, so keep your copy out of git.
- **claude.ai:** a custom connector needs a public HTTPS address and OAuth: see [Live deployment](#live-deployment). Claude Desktop can use stdio (above).

**What's different over HTTP:**
- **No file access:** the server can't see your files, so `file_path` and `output_path` are refused with an error that says to send the data inline. `clean_dataset` returns the cleaned data in the result.
- **Request size:** a request can carry up to 16 MB (`DATA_QUALITY_MCP_MAX_HTTP_REQUEST_MB`). A larger one gets HTTP `413` before any tool runs, so the agent sees a transport error, not a tool error. A CSV can use almost all of it, minus the JSON envelope and escaping. Base64 adds a third, so an `.xlsx` can be about 12 MB.
- **Host check:** on loopback the SDK rejects requests whose `Host` or `Origin` isn't local (DNS-rebinding protection). On a public interface, set `DATA_QUALITY_MCP_ALLOWED_HOSTS` (comma-separated, e.g. `mcp.example.com,mcp.example.com:443`) to the hostnames clients use; in OAuth mode it defaults to the public URL's host. `/health` isn't host-checked, so a container's health check keeps working.
- **TLS:** the server speaks plain HTTP. Anywhere beyond localhost it must sit behind TLS (a hosting platform or a reverse proxy), or the token travels in clear text.
- **Logs** go to stderr: one line per request with the method, the path (escaped, without the query string) and the status. Tokens, their claims, headers and bodies are never logged. Requests without a token are logged too, so a public deployment should rate-limit or rotate logs.

**Sizing** (peak memory of the server process, measured in the Docker image):

| Input | Peak |
|---|---|
| Idle server | ~170 MiB |
| 15.4 MiB CSV over HTTP (302,000 rows × 8 columns) | ~345 MiB |
| 14.7 MiB of base64 `.xlsx` (290,000 rows × 14 numeric columns) | ~435 MiB |
| 2.9 MiB of base64 `.xlsx` of long text, unpacking to 191 MiB | ~600 MiB |
| 46 MB CSV over stdio (499,000 rows × 12 columns) | ~500 MiB |

Plan for about 1 GiB for one call at a time. Concurrent calls each need their own memory and add up: the server is single-tenant, with no concurrency cap. Lower the limits below for a smaller container: [`docs/deployment.md`](docs/deployment.md#limits-on-the-free-instance) has the values and figures for 512 MB and a tenth of a CPU.

## Docker

```bash
make docker-build   # docker build -t data-quality-mcp .
make docker-run     # docker run --rm --env-file .env -p 8000:8000 data-quality-mcp
```

The image (Python 3.13, installed exactly from `uv.lock`) runs as a non-root user and serves HTTP on port 8000, with a health check on `/health`. The token (or the OAuth settings) is passed at run time and never stored in the image; without an auth mode the container exits at startup. It is built and smoke-tested in CI, in both modes, and isn't published to a registry: the live host builds it from the `Dockerfile`.

## Live deployment

The server runs on a free [Render](https://render.com) web service, built from this repo's `Dockerfile` ([`render.yaml`](render.yaml)), with sign-in through [WorkOS AuthKit](https://workos.com/docs/authkit/mcp). It accepts **only its owner**: anyone can sign in to AuthKit, but the server refuses every token whose user isn't the configured owner. To run your own, follow [`docs/deployment.md`](docs/deployment.md); it costs nothing, as long as no payment method is ever added to either account.

**Connect from claude.ai:** Settings → Connectors → Add custom connector, with the URL `https://<name>.onrender.com/mcp`, then sign in when asked. A connector's auth settings can't be edited afterwards: to change them, remove the connector and add it again.

**Connect from Claude Code:** `claude mcp add --transport http data-quality https://<name>.onrender.com/mcp`, then run `/mcp` in Claude Code to sign in.

**Cold starts:** the free service sleeps after 15 minutes without traffic and takes about a minute to wake. Claude gives up on the sign-in steps after 10 seconds, so the first connection after a sleep can fail. Open `https://<name>.onrender.com/health` in a browser first, and connect once it answers.

**Size:** the free instance has 512 MB and a tenth of a CPU, so the live server's limits are lower than the defaults (see [`docs/deployment.md`](docs/deployment.md#limits-on-the-free-instance)). It runs one large call at a time: two at once can run out of memory, and Render then restarts it.

## Limits

These limits can be changed with environment variables, on both transports:

| Variable | Default |
|---|---|
| `DATA_QUALITY_MCP_MAX_FILE_SIZE_MB` | 50 |
| `DATA_QUALITY_MCP_MAX_ROWS` | 500,000 |
| `DATA_QUALITY_MCP_MAX_XLSX_UNCOMPRESSED_MB` | 200 |
| `DATA_QUALITY_MCP_MAX_INLINE_OUTPUT_MB` | 1 |
| `DATA_QUALITY_MCP_MAX_HTTP_REQUEST_MB` | 16 |

Each must be a positive number (a whole number for rows). Overrides in effect are logged at startup. An invalid value stops the server with a message naming the variable, on stdio too: in Claude Desktop the server then shows as failed, and the message is in its MCP log, `mcp-server-data-quality.log` in Claude's `logs` folder (`%APPDATA%\Claude\logs`, or `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\logs` for the Microsoft Store version).

## Development

```bash
make dev            # MCP Inspector in the browser (needs Node.js)
make lint           # ruff check + format check (what CI runs)
make format         # auto-fix + format
make generate-data  # regenerate synthetic fixtures
```

Architecture and design rules are in [`docs/architecture.md`](docs/architecture.md) and [`CLAUDE.md`](CLAUDE.md). The key rule: the engines (`profiler_core/`, `cleaner_core/`) never import the MCP SDK or a web framework. Only `tools/` and `server.py` touch the SDK, and a test enforces this.

Every change goes through a pull request. CI runs commitlint on the PR title, lint, tests (Ubuntu and Windows, Python 3.10–3.14), the package and Docker image builds (with HTTP smoke tests of the image, with a token and in OAuth mode), security scans (pip-audit, Bandit, CodeQL) and an automated Claude review.

## License

MIT
