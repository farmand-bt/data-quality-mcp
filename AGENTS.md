# AGENTS.md

Instructions for Codex (local CLI and GitHub code review) working on this repository. The full project rules are in [CLAUDE.md](CLAUDE.md), and each milestone's spec is in `planning/`. Read both before judging a change.

`data-quality-mcp` is an MCP server that exposes deterministic dataset profiling and cleaning as tools. The profiling and cleaning logic is ported from the sibling project `llm-data-quality-analyzer`, and every intentional difference is listed in the milestone spec's Deviations table.

## Commands

- Tests with coverage: `uv run pytest --cov` (the gate is 85%)
- Lint and format check: `uv run ruff check .` and `uv run ruff format --check .`
- CI runs Python 3.10 (pandas 2) through 3.14 (pandas 3), on Ubuntu and Windows.

## Code Review Rules

Report only real problems, and give the failure scenario. Treat these as **P0**:
- `profiler_core/`, `cleaner_core/` or `config.py` importing `mcp` (the layer boundary)
- cleaning that mutates its input, overwrites an existing file, or can leave a partial output file
- tool output that isn't strict JSON (NaN, NaT, numpy types) or doesn't match `profiler_core/schema.py`
- a path check that can be bypassed (UNC or device paths on Windows, checks done after a filesystem call)

Treat these as **P1**:
- `print()` anywhere in `src/` (stdout is the JSON-RPC channel over stdio)
- logic inside `tools/` that belongs in the core, or domain errors not mapped to an actionable tool error
- uncomputed fields omitted or zero-filled instead of `null` (`0` means "checked, none found")
- behaviour that differs from the analyzer without a row in the spec's Deviations table, or that contradicts the spec
- code that only works on one of pandas 2 or pandas 3
- new behaviour or a bug fix without a test
- thresholds or limits read as globals inside core logic instead of keyword parameters defaulting to `config.py`

Don't report style that `ruff` already enforces.
