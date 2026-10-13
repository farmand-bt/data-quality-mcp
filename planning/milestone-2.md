# Milestone 2: Expanded Tool Surface

> **Status: draft for review.** Merging this spec approves its decisions and the schema changes in it; implementation starts afterwards in plan mode.
> Shared context (architecture, report schema, design decisions) is in [project-overview.md](project-overview.md). Milestone 1 is in [milestone-1.md](milestone-1.md).

**Goal:** Fill in the report sections Milestone 1 left `null` (outliers, duplicates, correlations, recommendations), make them part of the quality score, and use the rest of the MCP surface: focused tools, resources, and a prompt.

**Layer boundary:** all analysis is new pure-Python modules in `profiler_core/`. `tools/` gains thin adapters plus two new modules (`resources.py`, `prompts.py`), each with its own `register(app)`. `tests/test_architecture.py` is unchanged and must keep passing.

---

## Decisions to confirm in review

Each decision has a recommendation, which this spec assumes. Change any of them in review and the spec gets updated before merge.

| # | Decision | Recommendation | Alternative |
|---|---|---|---|
| D1 | Do duplicates and outliers count against the quality score from this milestone on? | **Yes.** The formula already has slots for them (see `docs/architecture.md`). The messy sample drops from 84 (B) to **72 (C)**. Release notes and the docs will say scores dropped. | Report them but keep the score M1-only until M5 |
| D2 | What drives the outlier penalty? | **% of rows with at least one IQR outlier in any numeric column**, × 2, capped at 10. Easy to explain ("4% of rows contain an outlier") and not skewed by which columns happen to have outliers. Messy sample: 22 of 520 rows (4.23%), penalty 8.5. | The analyzer's "average IQR % over columns that have outliers" (penalty 4.2 on the messy sample, so it would score 76). One stray outlier in one column scores as badly as heavy outliers everywhere. |
| D3 | Which z-score? | **Robust (modified) z-score:** 0.6745 × (x − median) / MAD, flagged above **3.5** (Iglewicz & Hoaglin). The classic mean/std z-score *masks* extreme values: on the messy sample it finds only 2 of the 10 injected price outliers, because the 9,999,999 values inflate the std. The robust z-score finds exactly those 10. The JSON field names stay `zscore_*`; the docs and tool docstring say it's the robust variant. `OUTLIER_ZSCORE_THRESHOLD` changes from 3.0 to 3.5. Falls back to classic z when MAD = 0. | Classic z-score at 3.0, as in the analyzer |
| D4 | Large row-index lists | **Cap each index list at 100** (`MAX_REPORTED_INDICES` in config). Counts and percentages stay exact, and `indices_truncated: true` says when a list was cut. A 500k-row file must not produce a multi-MB tool result. | Uncapped lists |
| D5 | Which resource(s)? | **Stateless resources:** `dataquality://schema/report` (the report's JSON Schema) and `dataquality://docs/quality-score` (how the score and grade are computed, as Markdown). They help an agent interpret results, and they behave the same over stdio and HTTP. | A `report://last` resource caching the most recent report. That's server-side state: harmless over stdio, but with several HTTP clients (M4) it leaks one caller's data to another, so it would need per-session storage. Deferred. |
| D6 | Near-duplicates | **Include** rows identical on every column but one (skipped above 50 columns, as in the analyzer). Reported only, not part of the score: they're often legitimate. | Exact duplicates only |
| D7 | Tool surface | **Keep `profile_dataset` as the full report, and add three focused tools** returning only their own section: `detect_outliers`, `find_duplicates`, `analyze_correlations`. An agent with a narrow question gets a small, targeted result. Recommendations come only with the full report, because they need every section. | Only `profile_dataset` |

---

## Schema changes (proposed)

These become the contract in `planning/project-overview.md` and `profiler_core/schema.py` once this spec is merged. Row indices are **0-based positions of data rows** (header excluded): index `i` is line `i + 2` of a CSV file.

Example values below are the real results for the `price` column and the duplicates of `tests/fixtures/messy_sample.csv`.

**Per-column `outliers`** (`OutlierInfo`). This is `null` only when outlier detection doesn't apply: non-numeric columns, or fewer than 4 numeric values.

```python
{
    "iqr_count": 15, "iqr_pct": 2.88,
    "iqr_lower_bound": 147204.5, "iqr_upper_bound": 546016.5,
    "iqr_indices": [3, 41, ...],
    "zscore_count": 10, "zscore_pct": 1.92,      # robust (modified) z-score, see D3
    "zscore_indices": [3, 41, ...],
    "indices_truncated": False,                  # True if any list above hit MAX_REPORTED_INDICES
}
```

**`duplicates`** (`DuplicateInfo`):

```python
{
    "exact_count": 19, "exact_pct": 3.65,        # rows that would be dropped, keeping the first copy
    "sample_indices": [...],                     # every row that belongs to a duplicate group (capped)
    "near_duplicate_count": 2, "near_duplicate_pct": 0.38,
    "near_sample_indices": [...],                # capped
    "indices_truncated": False,
}
```

**`correlations`** (`CorrelationInfo`):

```python
{
    "pearson": {"price": {"price": 1.0, "sqft": 0.02, ...}, ...},       # numeric/mixed columns; null cells where undefined
    "cramers_v": {"neighborhood": {"garage": 0.04}, ...},               # categorical/boolean pairs, upper triangle only
    "high_correlation_pairs": [
        {"col_a": "a", "col_b": "b", "method": "pearson", "value": 0.97},  # |value| >= HIGH_CORRELATION_THRESHOLD, strongest first
    ],
}
```

Limits: Pearson uses at most 50 numeric columns, and Cramér's V at most 15 categorical columns, each with at most 50 distinct values. Columns beyond the limits are skipped, and the docstring says so.

**`recommendations`** (`list[Recommendation]`), sorted critical → warning → info:

```python
{"severity": "critical" | "warning" | "info",
 "category": "missing" | "type" | "consistency" | "duplicate" | "outlier" | "correlation",
 "column": "price" | None,                       # None = dataset-level
 "message": "15 outliers (2.9%, IQR method): review before modeling; ..."}
```

The rules are ported from the analyzer's `recommendations.py`, plus `consistency` for inconsistent category labels. Messages say what to do next.

**Per-column `warnings`:** add an outlier warning when a column's IQR outlier share is above 5%, as in the analyzer.

## Quality score (with D1 + D2)

| Component | Penalty | Cap |
|---|---|---|
| Completeness | 2 × overall missing-cell % | 40 |
| Type consistency | 20 × mismatched columns / all columns | 20 |
| Duplicates | 1 × exact-duplicate-row % | 10 |
| Outliers | 2 × % of rows with ≥ 1 IQR outlier | 10 |

`compute_quality_score()` already takes `duplicate_pct` and `outlier_pct`. Only the inputs change (`outlier_pct` is now row-based), and `docs/architecture.md` is updated to match.

---

## Tasks

0. **Before writing code (plan mode):** check against the installed SDK and current docs:
   - How to register resources and prompts from a `register(app)` function: `app.resource(uri, ...)(fn)` / `app.add_resource(...)`, and `app.prompt(...)(fn)` / `app.add_prompt(Prompt.from_function(...))`
   - The `resource_security` defaults (they only matter for templated URIs; D5 uses static ones)
   - The `mime_type` handling for JSON and Markdown resources
   - `Client.list_resources` / `read_resource` / `list_prompts` / `get_prompt`, for the tests
1. **Config:** add `MAX_REPORTED_INDICES = 100`, `MAX_CORRELATION_NUMERIC_COLUMNS = 50`, `MAX_CRAMERS_V_COLUMNS = 15`, `MAX_CRAMERS_V_CARDINALITY = 50`, `MAX_NEAR_DUPLICATE_COLUMNS = 50`, `OUTLIER_COLUMN_WARNING_PCT = 5.0`. Change `OUTLIER_ZSCORE_THRESHOLD` to 3.5 (D3).
2. **`profiler_core/outliers.py`:** `detect_outliers(df, type_info, *, iqr_multiplier, zscore_threshold, max_indices) -> dict[str, OutlierInfo | None]`, plus a row-level helper returning the % of rows with any IQR outlier (for D2). Port from the analyzer, with the robust z-score (D3) and capped indices (D4).
3. **`profiler_core/duplicates.py`:** `analyze_duplicates(df, *, max_indices, max_near_duplicate_columns) -> DuplicateInfo`. Port, including near-duplicates (D6).
4. **`profiler_core/correlations.py`:** `compute_correlations(df, type_info, *, threshold, ...) -> CorrelationInfo`. Port Pearson and Cramér's V (scipy `chi2_contingency`), with the column and cardinality limits.
5. **`profiler_core/recommendations.py`:** `generate_recommendations(report, *, missing_threshold) -> list[Recommendation]`. Port the rules, add `consistency`, and sort by severity.
6. **`schema.py` + `report.py`:** add the new TypedDicts and wire the four modules into `generate_report`. Every section is computed now. Add the outlier warning and the new score inputs. Expose the section-level functions so the focused tools can call them directly.
7. **`tools/profiling_tools.py`:**
   - Add `detect_outliers(file_path)`, `find_duplicates(file_path)` and `analyze_correlations(file_path)`. Each returns `{"dataset": {...basic info, no score...}, "<section>": ...}` with its own TypedDict return type.
   - Share one small load-and-map-errors helper.
   - Mark all of them read-only.
   - Update `profile_dataset`'s docstring: null now means "not applicable" (outliers on non-numeric columns), not "not computed".
8. **`tools/resources.py`:** the two static resources (D5). The schema resource is generated from `DatasetReport` with Pydantic's `TypeAdapter(...).json_schema()`, so it can't drift from the code. The score doc is a Markdown string kept next to the formula.
9. **`tools/prompts.py`:** a `review_data_quality(file_path)` prompt. It asks the agent to call `profile_dataset`, triage the recommendations by severity, explain the top issues in plain language, and propose an ordered cleaning plan. Static text only, with no data access in the prompt.
10. **`server.py`:** register the new modules and update `INSTRUCTIONS` to mention the focused tools, resources and prompt.
11. **Fixtures:** keep `messy_sample.csv` unchanged so the M1 expectations stay valid. If correlation tests need a strongly correlated pair, add a small separate fixture (for example `tests/fixtures/correlated_sample.csv`) via the generator script.
12. **Tests:**
    - Unit tests per new core module, including edge cases: constant column (IQR = 0 and MAD = 0), fewer than 4 values, all-NaN column, single-category column, more columns than the limits, index caps with `indices_truncated`.
    - Fixture expectations: 19 exact and 2 near duplicates; 15 IQR and 10 robust-z outliers in `price`; 22 rows (4.23%) with an IQR outlier; score 72 (C).
    - Score and grade on the messy sample under the new formula.
    - `json.dumps(report, allow_nan=False)` still passes.
    - `test_tools.py`: the new tools are listed with output schemas and return only their section; `list_resources` and `read_resource` work for both URIs, and the schema resource parses as JSON Schema; `list_prompts` and `get_prompt` work; bad paths give actionable errors for every tool.
    - Coverage stays ≥ 85%.
13. **Docs:**
    - `planning/project-overview.md`: schema example
    - `docs/architecture.md`: score table, outlier methods, index semantics, resources and prompt
    - README tools table, plus a "Resources & prompts" section
    - `planning/milestone-2.md`: status and as-built notes
14. **Verify end-to-end:** the MCP Inspector (`make dev`) shows 4 tools, 2 resources and 1 prompt. Run every tool on the messy sample, then run the prompt with that file's path.

**Out of scope:** tools that accept `inline_content` (M3), cleaning tools (M3), distribution and normality analysis (could be a later addition; not in the schema), HTTP transport (M4).

**CI / ruleset:** no new workflows or job names, so the ruleset's required checks don't change.

**PR title / squash commit message:** `feat: outlier, duplicate and correlation analysis with recommendations, resources and prompt`
