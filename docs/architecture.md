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
cleaner_core/       pure Python cleaning engine: handlers, pipeline, writer, run_cleaning.
        │           Uses profiler_core. Never imports `mcp`.
profiler_core/      pure Python engine (pandas/numpy): loader, type detection, stats,
config.py           missing values, report, schema. Never imports `mcp`.
```

**The rule:** only `server.py` and `tools/` may import the MCP SDK. `profiler_core/`, `cleaner_core/` and `config.py` never do. `tests/test_architecture.py` parses every core file and fails the build if one imports `mcp`. Because of this, adding the HTTP transport in Milestone 4 means adding an entry point, not rewriting the engine.

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

The outlier, duplicate, correlation and recommendation logic and the cleaning handlers are ported from `llm-data-quality-analyzer` with the same methods, thresholds and rules, so both projects give the same answers on the same data. The ported test cases live in the `TestAnalyzerParity` classes. Every deviation, with its reason, is listed in the milestone specs:
- [`planning/milestone-2.md`](../planning/milestone-2.md#deviations-from-the-analyzer) for profiling
- [`planning/milestone-3.md`](../planning/milestone-3.md#deviations-from-the-analyzer) for cleaning

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
  - **Infinite values:** quartiles, mean and std are computed from finite values only. ±inf is always flagged by both methods, counted in the column's `stats.infinite_count`, and named in a column warning. Fences are reported rounded to 10 significant digits (no float noise like `-3.6999999999999993`, and small-scale fences keep their precision); comparisons use the exact fences. With fewer than 4 finite values, only the infinite values are flagged and the fences are `null`.
  - **Classic z-score (secondary):** `|x − mean| / std > 3`. Extreme values inflate the std and can mask other outliers. On the messy sample, z flags 2 price outliers where IQR flags 15. It is kept for parity with the analyzer, and the tool docstrings tell agents to prefer IQR.
- **Duplicates:**
  - exact duplicates: `exact_count` is how many rows deduplication would remove
  - near-duplicates: rows identical on all columns but one, excluding exact duplicates, skipped above 50 columns. They're reported but not scored. Each cell is hashed once, and a row's "all columns but one" key is the wrapping sum of its column hashes minus one term, so each column needs only one cheap uint64 pass. Hash matches are confirmed on the real values, so the result is exactly the analyzer's definition (a randomized test checks this). At the limits (500k rows × 50 columns), this takes about 1.6 s, compared with 21.6 s for the per-column full-row passes.
- **Correlations:** Pearson over the first 50 numeric columns, and Cramér's V (`scipy` χ², no bias correction) over the first 15 categorical/boolean columns with at most 50 distinct values. A pair needs at least 10 rows with both values, otherwise it is `null` (two rows always give r = ±1). Pairs with |value| ≥ 0.9 are listed strongest first.

## Heuristics added in Milestone 3.5

Each one reports evidence, not a verdict, and was checked against the problems `scripts/generate_messy_data.py` injects ([milestone-3.5.md](../planning/milestone-3.5.md) has the measurements).

- **What missing values go with** (`missing_related_to`, `profiler_core/missing.py`):
  - **What is tested:** each column with gaps (the first 50) against numeric columns (point-biserial r), categorical/boolean columns (bias-corrected Cramér's V, rare categories left out) and other columns' missing indicators (positive phi). Each pair needs 10 missing and 10 present rows of its own.
  - **What is listed:** |value| ≥ 0.3 and p < 0.001 / (pairs tested for that column), a Bonferroni correction.
  - **Calibration:** under random gaps it listed nothing in any calibration run, up to about 100 comparison columns.
  - **Limits:** it can't show that gaps are random (MCAR) or depend on the missing values themselves (MNAR), so no label is given. `null` means not tested, and `[]` means nothing found.
- **Possible placeholder codes** (`possible_sentinels` in `OutlierInfo`, `profiler_core/outliers.py`):
  - **The rule:** an IQR outlier that repeats on 2+ rows once exact duplicate rows are removed, in a column whose unique ratio is ≥ 0.5; or an integer of three or more 9s.
  - **Limits:** a legitimate repeated extreme or a real 999 is listed too. A code inside the normal range, or in a column with few distinct values, is missed.
  - **Cleaning:** happens only through `method: "sentinel"` with the codes the caller confirmed.
- **Repeated IDs** (`key_columns` in `DuplicateInfo`, `profiler_core/duplicates.py`):
  - **What counts as a key:** a column named `id`/`key`/`uuid`/`guid`, or ending in one after `_`, `-`, a space or a camelCase boundary. Its values must also be ≥ 95% distinct once exact duplicates are removed, which keeps foreign keys out.
  - **What is reported:** IDs found on rows with different data.
- **ISO dates in a CSV** (`type_detector.py`): text that is all ISO 8601, in one timezone convention, isn't a type mismatch for CSV input, which has no date type. The report builders get the format from the loader's extension. XLSX text dates, other formats and mixed formats stay mismatches.
- **Not scored:** the score formula is unchanged. These findings describe values that are already scored (missing, outliers) or are judgement calls (repeated IDs).
- **Speed:** at 500,000 × 10, `profile_dataset` takes about 3.8 s (3.0 s before Milestone 3.5). Type detection parses each distinct value once, and the deduplicated-row mask is computed once per report.

## Input

Every tool takes `file_path`, or `inline_content` plus `filename_hint`. Both go through `loader.load_input`:
- **Inline formats:** CSV is plain text and XLSX is base64. A single-line base64 payload sent as CSV is rejected with a hint, so a client using the old format gets an error rather than a one-column parse.
- **Limits:** 50 MB and 500,000 rows. For XLSX, the zip is also checked before parsing: at most 200 MB unpacked and 1,000 entries. Python's `zipfile` stops at each entry's declared size, so the declared total bounds memory.
- **Paths:** on Windows, UNC and device paths are rejected on the raw string before any filesystem call (`profiler_core/paths.py`), because even a stat on `\\host\share` connects to that host. `config.ALLOW_NETWORK_PATHS` opts back in.

## Cleaning

`clean_dataset` passes its arguments to `cleaner_core.run_cleaning`, which works in this order:

1. **Path checks** that need no I/O.
2. **Load**, then `prepare_frame` (the report's column names and a 0-based index).
3. **`before`**, from `generate_report`.
4. **Validate and run** the steps (`pipeline.run_steps`).
5. **Serialise** in memory.
6. **`after`**, from the serialised bytes loaded back, so it equals `profile_dataset` on the output.
7. **Write** the file last, or return the content inline.

Nothing is written unless every earlier stage succeeded.

- **Handlers** (`missing_handler`, `duplicate_handler`, `type_fixer`, `outlier_handler`) are the analyzer's, with a `reset_index` switch.
- **Step order** is the analyzer's stable priority: type fixes, fills, `cap_iqr` and `replace_nan` first, then outlier `remove`, then `drop_rows`, `drop_column` and `remove_duplicates`. Outlier rows are computed on the input by the report's own function, uncapped. Original row labels are kept until every step has run, so two outlier removals both hit the right rows.
- **Validation.** Static rules are checked before the first step runs:
  - the column exists
  - at most one step per action and column, as the analyzer's panel allows
  - no step on a column that `drop_column` removes
  - outlier steps only on columns where detection applies in the input
  - `method: "sentinel"` needs `values`, each a possible sentinel of the input column, and doesn't combine with `cap_iqr`
  - `fill_constant` needs a value, and options with no effect are rejected

  Type-dependent rules are checked when their step runs:
  - `fill_mean`/`fill_median` need at least one finite number, and use finite values only (Milestone 3.5)
  - `lowercase` needs a categorical column
  - `cap_iqr` needs at least 4 finite values
  - `fill_constant` values are converted to the column's type

  Steps that would leave no rows or no columns are rejected.
- **Log:** one entry per step, with every field present and `null` where it doesn't apply. `values_nulled` counts cells that became null, such as a failed type conversion.
- **Writer:**
  - **Output name:** `<stem>_cleaned.<ext>` next to the input, and the next free `_N` if that name is taken.
  - **No overwrites:** the file is opened with exclusive create (`"xb"`), so even racing calls can't overwrite each other.
  - **No partial files:** a failed write deletes what it created.
  - **CSV:** UTF-8, no index column, `\n` line endings.
  - **XLSX:** timezone-aware datetimes become naive UTC.
  - **Inline output** is capped at 1 MB of encoded text.

At 500,000 rows × 10 columns, `clean_dataset` takes about 8 s, including two full reports (`profile_dataset` alone: about 4 s).

## MCP surface

| Kind | Name | Purpose |
|---|---|---|
| Tool | `profile_dataset` | Full report, including recommendations and score |
| Tool | `detect_outliers` / `find_duplicates` / `analyze_correlations` | One section each, plus `dataset` basics. Smaller results for narrow questions |
| Tool | `clean_dataset` | Cleaning steps → new file or inline content, log, quality before/after |
| Resource | `dataquality://schema/report` | JSON Schema of the full report (`application/schema+json`) |
| Resource | `dataquality://docs/quality-score` | How the score, grade and outlier methods work (`text/markdown`) |
| Prompt | `review_data_quality(file_path)` | Guides an agent through a full review and an ordered cleaning plan |

The resources are **stateless**: there's no "last report" cache. Server-side state would leak one caller's data to another once several HTTP clients connect (Milestone 4). The profiling tools are annotated read-only and closed-world. `clean_dataset` is annotated not read-only, but also not destructive (it never overwrites) and not idempotent (each call writes a new file).

## Compatibility

- Python 3.10–3.14. pandas 3 requires Python ≥ 3.11, so Python 3.10 runs pandas 2. String columns report `pandas_dtype` as `object` on pandas 2 and `str` on pandas 3. CI tests both lines.
- MCP Python SDK v2 (`mcp>=2.2,<3`). v2 renamed v1's `FastMCP` to `MCPServer`.
