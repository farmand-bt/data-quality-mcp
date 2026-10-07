# CLAUDE.md

Data Quality MCP Server: an MCP server that exposes deterministic dataset profiling and cleaning as tools for any MCP client. The full plan and milestone roadmap are in `data-quality-mcp-server-overview.md`. Read it before starting a milestone.

## Invariants (apply to every milestone)

### Layer boundary (most important)
- `profiler_core/`, `cleaner_core/`, and `config.py` are pure Python and **never import `mcp`**. Only `tools/` and `server.py` may touch the MCP SDK.
- `tests/test_architecture.py` enforces this. Never weaken, skip, or work around it.
- Registration pattern: each `tools/*.py` module defines plain functions plus `register(app: FastMCP) -> None`. `server.py` creates the app and calls each `register(app)`. Tool modules never import a global app from `server.py`.
- Tools are thin adapters: parse args, call the core, shape the response. If a tool function has real logic in it, that logic belongs in the core.

### Errors
- The core raises domain exceptions from `profiler_core/exceptions.py`. It never raises or builds MCP-specific errors.
- `tools/` turns domain exceptions into clear tool errors that tell the calling agent how to fix the call.

### Output
- Tool output must be JSON-safe: only native Python types, `NaN`/`NaT` become `None`, timestamps are UTC ISO 8601 strings.
- Fields that haven't been computed yet are present and set to `null`, never omitted and never zero-filled (`0` means "checked, none found").
- Follow the report schema in the overview doc exactly. Change it only with explicit approval.

### stdio hygiene
- No `print()` anywhere in `src/`. Over stdio, stdout is the JSON-RPC channel. Log to stderr with the `logging` module.

### Configuration
- Defaults live in `config.py`. Core functions take thresholds and limits as keyword parameters that default to the config values, rather than reading globals deep inside the logic.
- Size and row limits are always enforced in the loader.

### Behavior
- No LLM calls anywhere. The server is deterministic tooling.
- Cleaning (Milestone 3+) is non-destructive: never mutate the input in place, and log every transformation (what was done, which column, how many rows were affected).

### Tool docstrings
- FastMCP builds the schema and description an agent sees from type hints and docstrings. Write them for a language model reader: when to use the tool, what each argument means, what comes back, and what the common errors are.

## Working conventions
- Platform: Windows 11 (PowerShell, and Git Bash is available). Commands and client configs must work on Windows. Client configs use absolute paths to the venv interpreter or `uv`.
- MCP tooling changes fast. Check SDK APIs, Inspector commands, and client config formats against current docs instead of relying on memory.
- Before committing: `ruff check --fix .`, `ruff format .`, `pytest`.
- Commit messages and PR titles follow Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`). Use `docs:`, not `doc:`, because commitlint rejects `doc:`.
- Every change goes through a PR: create a branch, open a PR, wait for the CI checks to pass, then squash-merge. Never push directly to `main`. The PR title becomes the squash commit message, so it must pass commitlint.
- Use plan mode at the start of each milestone. The plan should state how the milestone respects the layer boundary.
