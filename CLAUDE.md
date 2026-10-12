# CLAUDE.md

Data Quality MCP Server: an MCP server that exposes deterministic dataset profiling and cleaning as tools for any MCP client. The full plan and milestone roadmap are in `data-quality-mcp-server-overview.md`. Read it before starting a milestone.

## Invariants (apply to every milestone)

### Layer boundary (most important)
- `profiler_core/`, `cleaner_core/`, and `config.py` are pure Python and **never import `mcp`**. Only `tools/` and `server.py` may touch the MCP SDK.
- `tests/test_architecture.py` enforces this. Never weaken, skip, or work around it.
- Registration pattern: each `tools/*.py` module defines plain functions plus `register(app: MCPServer) -> None` (MCP SDK v2: `from mcp.server import MCPServer`; v1's `FastMCP` no longer exists). `server.py` creates the app and calls each `register(app)`. Tool modules never import a global app from `server.py`.
- Tools are thin adapters: parse args, call the core, shape the response. If a tool function has real logic in it, that logic belongs in the core.

### Errors
- The core raises domain exceptions from `profiler_core/exceptions.py`. It never raises or builds MCP-specific errors.
- `tools/` turns domain exceptions into clear tool errors that tell the calling agent how to fix the call.

### Output
- The report contract lives in `profiler_core/schema.py` (TypedDicts from `typing_extensions`, which Pydantic requires on Python < 3.12).
- Tool output must be JSON-safe (use `profiler_core/json_safe.to_json_safe`): only native Python types, `NaN`/`NaT` become `None`, timestamps are UTC ISO 8601 strings.
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
- `MCPServer` builds the schema and description an agent sees from type hints and docstrings (per-argument descriptions via `Annotated[T, Field(description=...)]`). The output schema comes from the return type, i.e. the TypedDicts in `profiler_core/schema.py`, and results are validated against it. Write them for a language model reader: when to use the tool, what each argument means, what comes back, and what the common errors are.

## Working conventions
- Platform: Windows 11 (PowerShell, and Git Bash is available). Commands and client configs must work on Windows. Client configs use absolute paths to the venv interpreter or `uv`.
- MCP tooling changes fast. Check SDK APIs, Inspector commands, and client config formats against current docs instead of relying on memory.
- Tooling: `uv` + GNU `make`. Before committing: `make format` then `make test` (`make lint` is what CI runs).
- Code must work on both pandas 2 (Python 3.10) and pandas 3 (Python 3.11+); for example, string columns are dtype `object` on pandas 2 and `str` on pandas 3. CI tests both.
- Commit messages and PR titles follow Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`). Use `docs:`, not `doc:`, because commitlint rejects `doc:`.
- Every change goes through a PR: create a branch, open a PR, wait for the CI checks to pass, then squash-merge. Never push directly to `main`. The PR title becomes the squash commit message, so it must pass commitlint.
- Use plan mode at the start of each milestone. The plan should state how the milestone respects the layer boundary.
